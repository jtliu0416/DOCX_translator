using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;

namespace Doctrans.DocxProc;

class Program
{
    static int Main(string[] args)
    {
        if (args.Length < 1)
        {
            Console.WriteLine("Usage: Doctrans.DocxProc <command> [options]");
            Console.WriteLine("Commands:");
            Console.WriteLine("  extract  --input <docx> --output <json>");
            Console.WriteLine("  insert   --input <docx> --translations <json> --output <docx> [--paragraphs <json>]");
            return 1;
        }

        string command = args[0];
        var opts = ParseArgs(args.Skip(1).ToArray());

        try
        {
            return command switch
            {
                "extract" => Extract(opts),
                "insert" => Insert(opts),
                _ => throw new Exception($"Unknown command: {command}")
            };
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Error: {ex.Message}");
            return 1;
        }
    }

    static Dictionary<string, string> ParseArgs(string[] args)
    {
        var dict = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (args[i].StartsWith("--"))
                dict[args[i].TrimStart('-')] = args[++i];
        }
        return dict;
    }

    // ─── Robust heading detection ────────────────────────────────

    /// <summary>
    /// Scans the document's style definitions and builds a map of style IDs
    /// that are heading styles. In OpenXML, a heading style is ANY style
    /// (regardless of name) that defines OutlineLevel 0–8 in its
    /// StyleParagraphProperties. This is the canonical, name-agnostic method.
    /// </summary>
    static Dictionary<string, int> BuildHeadingStyleMap(WordprocessingDocument doc)
    {
        var map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var stylesPart = doc.MainDocumentPart?.StyleDefinitionsPart;
        if (stylesPart?.Styles == null) return map;

        foreach (var style in stylesPart.Styles.Elements<Style>())
        {
            // Only paragraph styles
            if (style.Type != null && style.Type.Value != StyleValues.Paragraph) continue;

            var pPr = style.StyleParagraphProperties;
            var outlineLvl = pPr?.OutlineLevel;
            if (outlineLvl != null && outlineLvl.Val != null
                && outlineLvl.Val.Value >= 0 && outlineLvl.Val.Value <= 8)
            {
                // OutlineLevel 0 = Heading 1, 1 = Heading 2, etc.
                map[style.StyleId] = outlineLvl.Val.Value + 1;
            }
        }

        Console.WriteLine($"Found {map.Count} heading styles: {string.Join(", ", map.Keys)}");
        return map;
    }

    /// <summary>
    /// Detects heading using the style map (from styles.xml), with fallbacks
    /// for inline OutlineLevel and common naming patterns.
    /// </summary>
    static (bool isHeading, int level) DetectHeading(
        Paragraph para, string styleId, Dictionary<string, int>? headingStyleMap)
    {
        // Strategy 1 (primary): Style defines OutlineLevel — covers ALL heading styles
        if (headingStyleMap != null && headingStyleMap.TryGetValue(styleId, out var mapLevel))
        {
            return (true, mapLevel);
        }

        // Strategy 2: OutlineLevel set directly on the paragraph's pPr (inline override)
        var pPr = para.ParagraphProperties;
        if (pPr != null)
        {
            var outlineLvl = pPr.OutlineLevel;
            if (outlineLvl != null && outlineLvl.Val != null
                && outlineLvl.Val.Value >= 0 && outlineLvl.Val.Value <= 8)
            {
                return (true, outlineLvl.Val.Value + 1);
            }
        }

        // Strategy 3: Fallback common naming patterns
        if (Regex.IsMatch(styleId, @"(?i)^Heading\s*\d+$"))
        {
            var m = Regex.Match(styleId, @"\d+");
            return (true, m.Success ? int.Parse(m.Value) : 1);
        }
        if (Regex.IsMatch(styleId, @"^标题\s*\d+$"))
        {
            var m = Regex.Match(styleId, @"\d+");
            return (true, m.Success ? int.Parse(m.Value) : 1);
        }
        if (Regex.IsMatch(styleId, @"^[1-9]$"))
        {
            return (true, int.Parse(styleId));
        }

        return (false, 0);
    }

    // ─── Extract ────────────────────────────────────────────────
    static int Extract(Dictionary<string, string> opts)
    {
        string inputPath = opts["input"];
        string outputPath = opts["output"];

        var units = new List<object>();
        int idx = 0;

        using (var doc = WordprocessingDocument.Open(inputPath, false))
        {
            var body = doc.MainDocumentPart!.Document.Body!;
            var headingStyleMap = BuildHeadingStyleMap(doc);

            foreach (var para in body.Elements<Paragraph>())
            {
                string text = GetParaText(para);
                if (string.IsNullOrEmpty(text)) continue;

                var pPr = para.ParagraphProperties;
                string styleId = pPr?.ParagraphStyleId?.Val?.Value ?? "Normal";
                var (isHeading, level) = DetectHeading(para, styleId, headingStyleMap);

                bool hasZh = ContainsChinese(text);
                double enRatio = EnglishRatio(text);
                bool skip = !hasZh || (enRatio > 0.3 && hasZh);

                units.Add(new Dictionary<string, object?>
                {
                    ["index"] = idx,
                    ["type"] = isHeading ? "heading" : "paragraph",
                    ["level"] = level,
                    ["text"] = text,
                    ["style_id"] = styleId,
                    ["contains_chinese"] = hasZh,
                    ["already_translated"] = enRatio > 0.3 && hasZh,
                    ["skip"] = skip,
                });
                idx++;
            }

            int tIdx = 0;
            foreach (var table in body.Elements<Table>())
            {
                int rIdx = 0;
                foreach (var row in table.Elements<TableRow>())
                {
                    int cIdx = 0;
                    foreach (var cell in row.Elements<TableCell>())
                    {
                        string text = GetCellText(cell);
                        if (!string.IsNullOrEmpty(text))
                        {
                            bool hasZh = ContainsChinese(text);
                            double enRatio = EnglishRatio(text);
                            bool skip = !hasZh || (enRatio > 0.3 && hasZh);

                            units.Add(new Dictionary<string, object?>
                            {
                                ["index"] = idx,
                                ["type"] = "table_cell",
                                ["table_index"] = tIdx,
                                ["row_index"] = rIdx,
                                ["col_index"] = cIdx,
                                ["text"] = text,
                                ["contains_chinese"] = hasZh,
                                ["already_translated"] = enRatio > 0.3 && hasZh,
                                ["skip"] = skip,
                            });
                            idx++;
                        }
                        cIdx++;
                    }
                    rIdx++;
                }
                tIdx++;
            }
        }

        var result = new Dictionary<string, object> { ["units"] = units };
        var options = new JsonSerializerOptions { WriteIndented = true };
        File.WriteAllText(outputPath, JsonSerializer.Serialize(result, options));
        Console.WriteLine($"Extracted {units.Count} units to {outputPath}");
        return 0;
    }

    // ─── Insert ────────────────────────────────────────────────
    static int Insert(Dictionary<string, string> opts)
    {
        string inputPath = opts["input"];
        string transPath = opts["translations"];
        string outputPath = opts["output"];

        // Load translations
        var json = File.ReadAllText(transPath);
        var doc = JsonDocument.Parse(json);
        var transMap = new Dictionary<int, string>();
        foreach (var item in doc.RootElement.GetProperty("translations").EnumerateArray())
        {
            transMap[item.GetProperty("index").GetInt32()] =
                item.GetProperty("text").GetString() ?? "";
        }
        Console.WriteLine($"Loaded {transMap.Count} translations");

        // Load paragraph type map from extract output (if provided)
        var typeMap = new Dictionary<int, string>(); // index -> "heading" | "paragraph" | "table_cell"
        if (opts.TryGetValue("paragraphs", out var paragraphsPath) && File.Exists(paragraphsPath))
        {
            var pJson = File.ReadAllText(paragraphsPath);
            var pDoc = JsonDocument.Parse(pJson);
            foreach (var unit in pDoc.RootElement.GetProperty("units").EnumerateArray())
            {
                typeMap[unit.GetProperty("index").GetInt32()] =
                    unit.GetProperty("type").GetString() ?? "paragraph";
            }
            Console.WriteLine($"Loaded {typeMap.Count} paragraph types from extract");
        }

        File.Copy(inputPath, outputPath, true);

        using (var wordDoc = WordprocessingDocument.Open(outputPath, true))
        {
            var body = wordDoc.MainDocumentPart!.Document.Body!;
            var headingStyleMap = BuildHeadingStyleMap(wordDoc);
            int unitIdx = 0;

            var paragraphs = body.Elements<Paragraph>().ToList();
            var insertAfter = new List<(Paragraph src, Paragraph eng)>();

            foreach (var para in paragraphs)
            {
                string text = GetParaText(para);
                if (string.IsNullOrEmpty(text)) continue;

                bool hasZh = ContainsChinese(text);
                double enRatio = EnglishRatio(text);
                bool skip = !hasZh || (enRatio > 0.3 && hasZh);

                if (!skip && transMap.TryGetValue(unitIdx, out var engText))
                {
                    // Use type from extract if available, otherwise detect from styles
                    bool isHeading;
                    if (typeMap.TryGetValue(unitIdx, out var unitType))
                    {
                        isHeading = unitType == "heading";
                    }
                    else
                    {
                        var pPr = para.ParagraphProperties;
                        string styleId = pPr?.ParagraphStyleId?.Val?.Value ?? "Normal";
                        isHeading = DetectHeading(para, styleId, headingStyleMap).isHeading;
                    }

                    if (isHeading)
                    {
                        // Heading: append inline with space separator (same paragraph)
                        var srcRun = para.Elements<Run>().FirstOrDefault();
                        var spaceRun = new Run(new Text(" ") { Space = SpaceProcessingModeValues.Preserve });
                        para.AppendChild(spaceRun);
                        para.AppendChild(MakeEnglishRun(engText, srcRun?.RunProperties));
                    }
                    else
                    {
                        // Normal paragraph: insert new paragraph after
                        var newPara = MakeEnglishParagraph(para, engText);
                        insertAfter.Add((para, newPara));
                    }
                }
                unitIdx++;
            }

            // Insert new paragraphs in reverse order to preserve positions
            foreach (var (src, eng) in insertAfter.AsEnumerable().Reverse())
                src.InsertAfterSelf(eng);

            // Process tables
            foreach (var table in body.Elements<Table>())
            {
                foreach (var row in table.Elements<TableRow>())
                {
                    foreach (var cell in row.Elements<TableCell>())
                    {
                        string text = GetCellText(cell);
                        if (string.IsNullOrEmpty(text)) continue;

                        bool hasZh = ContainsChinese(text);
                        double enRatio = EnglishRatio(text);
                        bool skip = !hasZh || (enRatio > 0.3 && hasZh);

                        if (!skip && transMap.TryGetValue(unitIdx, out var engText))
                        {
                            var lastPara = cell.Elements<Paragraph>().LastOrDefault();
                            if (lastPara != null)
                            {
                                var srcRun = lastPara.Elements<Run>().FirstOrDefault();
                                lastPara.AppendChild(new Run(new Break()));
                                lastPara.AppendChild(MakeEnglishRun(engText, srcRun?.RunProperties));
                            }
                        }
                        unitIdx++;
                    }
                }
            }

            wordDoc.Save();
        }

        Console.WriteLine($"Bilingual DOCX saved to {outputPath}");
        return 0;
    }

    // ─── Helpers ──────────────────────────────────────────────

    static bool ContainsChinese(string text)
    {
        foreach (char c in text)
        {
            if (c >= '一' && c <= '鿿') return true;
            if (c >= '㐀' && c <= '䶿') return true;
        }
        return false;
    }

    static double EnglishRatio(string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return 0.0;
        int en = 0;
        foreach (char c in text)
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) en++;
        return (double)en / text.Trim().Length;
    }

    static string GetParaText(Paragraph p)
    {
        var sb = new System.Text.StringBuilder();
        foreach (var run in p.Elements<Run>())
            foreach (var t in run.Elements<Text>())
                sb.Append(t.Text);
        return sb.ToString().Trim();
    }

    static string GetCellText(TableCell cell)
    {
        var sb = new System.Text.StringBuilder();
        foreach (var p in cell.Elements<Paragraph>())
            sb.Append(GetParaText(p));
        return sb.ToString().Trim();
    }

    static RunProperties CloneRunPropsWithArial(RunProperties? src)
    {
        if (src == null)
        {
            return new RunProperties(
                new RunFonts { Ascii = "Arial", HighAnsi = "Arial", EastAsia = "Arial", ComplexScript = "Arial" }
            );
        }
        var clone = (RunProperties)src.CloneNode(true);
        var fonts = clone.Elements<RunFonts>().FirstOrDefault();
        if (fonts != null)
        {
            fonts.Ascii = "Arial";
            fonts.HighAnsi = "Arial";
            fonts.EastAsia = "Arial";
            fonts.ComplexScript = "Arial";
        }
        else
        {
            clone.InsertAt(new RunFonts
            {
                Ascii = "Arial", HighAnsi = "Arial",
                EastAsia = "Arial", ComplexScript = "Arial"
            }, 0);
        }
        return clone;
    }

    static Run MakeEnglishRun(string text, RunProperties? srcProps)
    {
        var rPr = CloneRunPropsWithArial(srcProps);
        var run = new Run();
        run.AppendChild(rPr);
        run.AppendChild(new Text(text) { Space = SpaceProcessingModeValues.Preserve });
        return run;
    }

    static Paragraph MakeEnglishParagraph(Paragraph srcPara, string engText)
    {
        var newPara = new Paragraph();
        if (srcPara.ParagraphProperties != null)
        {
            var clonedProps = (ParagraphProperties)srcPara.ParagraphProperties.CloneNode(true);
            // Remove auto-numbering to prevent inserted English paragraphs from
            // entering the numbering sequence and shifting subsequent numbers.
            clonedProps.NumberingProperties?.Remove();
            clonedProps.OutlineLevel?.Remove();
            newPara.AppendChild(clonedProps);
        }

        var srcRun = srcPara.Elements<Run>().FirstOrDefault();
        newPara.AppendChild(MakeEnglishRun(engText, srcRun?.RunProperties));
        return newPara;
    }
}

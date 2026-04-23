#r "nuget: DocumentFormat.OpenXml, 3.2.0"
#r "nuget: System.Text.Json, 8.0.0"

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;

// --- Parse CLI args ---
string inputPath = "";
string translationsPath = "";
string outputPath = "";

for (int i = 0; i < Args.Count; i++)
{
    if (Args[i] == "--input" && i + 1 < Args.Count) inputPath = Args[++i];
    if (Args[i] == "--translations" && i + 1 < Args.Count) translationsPath = Args[++i];
    if (Args[i] == "--output" && i + 1 < Args.Count) outputPath = Args[++i];
}

if (string.IsNullOrEmpty(inputPath) || string.IsNullOrEmpty(translationsPath) || string.IsNullOrEmpty(outputPath))
{
    Console.WriteLine("Usage: insert_translations.csx --input <docx> --translations <json> --output <docx>");
    Environment.Exit(1);
}

// --- Load translations ---
string jsonContent = File.ReadAllText(translationsPath);
var jsonDoc = JsonDocument.Parse(jsonContent);
var transMap = new Dictionary<int, string>();

foreach (var item in jsonDoc.RootElement.GetProperty("translations").EnumerateArray())
{
    int index = item.GetProperty("index").GetInt32();
    string text = item.GetProperty("text").GetString() ?? "";
    transMap[index] = text;
}

Console.WriteLine($"Loaded {transMap.Count} translations");

// --- Helpers ---
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
    {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) en++;
    }
    return (double)en / text.Trim().Length;
}

static string GetText(Paragraph p)
{
    string result = "";
    foreach (var run in p.Elements<Run>())
        foreach (var t in run.Elements<Text>())
            result += t.Text;
    return result.Trim();
}

static string GetCellText(TableCell cell)
{
    string result = "";
    foreach (var p in cell.Elements<Paragraph>())
        result += GetText(p);
    return result.Trim();
}

static RunProperties CloneRunPropsWithArial(RunProperties src)
{
    if (src == null)
    {
        return new RunProperties(
            new RunFonts { Ascii = "Arial", HighAnsi = "Arial", EastAsia = "Arial", ComplexScript = "Arial" }
        );
    }
    var clone = (RunProperties)src.CloneNode(true);
    // Override fonts to Arial
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
        clone.InsertAt(new RunFonts { Ascii = "Arial", HighAnsi = "Arial", EastAsia = "Arial", ComplexScript = "Arial" }, 0);
    }
    return clone;
}

static Run MakeEnglishRun(string text, RunProperties srcProps)
{
    var rPr = CloneRunPropsWithArial(srcProps);
    var run = new Run();
    run.AppendChild(rPr);
    var t = new Text(text) { Space = SpaceProcessingModeValues.Preserve };
    run.AppendChild(t);
    return run;
}

static Paragraph MakeEnglishParagraph(Paragraph srcPara, string engText)
{
    var newPara = new Paragraph();

    // Clone paragraph properties (style, alignment, spacing, etc.)
    if (srcPara.ParagraphProperties != null)
    {
        newPara.AppendChild((ParagraphProperties)srcPara.ParagraphProperties.CloneNode(true));
    }

    // Get source run properties for style inheritance
    var srcRun = srcPara.Elements<Run>().FirstOrDefault();
    var srcRunProps = srcRun?.RunProperties;

    // Create English run with Arial font
    newPara.AppendChild(MakeEnglishRun(engText, srcRunProps));

    return newPara;
}

// --- Main: Process document ---
// Copy input to output first, then modify in place
File.Copy(inputPath, outputPath, true);

using (var doc = WordprocessingDocument.Open(outputPath, true))
{
    var body = doc.MainDocumentPart.Document.Body;
    int unitIdx = 0;

    // Collect paragraphs to process (need list since we may insert)
    var paragraphs = body.Elements<Paragraph>().ToList();
    var insertAfter = new List<KeyValuePair<Paragraph, Paragraph>>();

    foreach (var para in paragraphs)
    {
        string text = GetText(para);
        if (string.IsNullOrEmpty(text)) continue;

        var pPr = para.ParagraphProperties;
        string styleId = pPr?.ParagraphStyleId?.Val?.Value ?? "Normal";
        bool isHeading = styleId.StartsWith("Heading", StringComparison.OrdinalIgnoreCase)
                      || styleId.StartsWith("heading", StringComparison.OrdinalIgnoreCase);

        bool hasZh = ContainsChinese(text);
        double enRatio = EnglishRatio(text);
        bool skip = !hasZh || (enRatio > 0.3 && hasZh);

        if (!skip && transMap.ContainsKey(unitIdx))
        {
            string engText = transMap[unitIdx];

            if (isHeading)
            {
                // Heading: append space + English run in same paragraph
                var srcRun = para.Elements<Run>().FirstOrDefault();
                var srcRunProps = srcRun?.RunProperties;

                // Space run
                var spaceRun = new Run();
                spaceRun.AppendChild(new Text(" ") { Space = SpaceProcessingModeValues.Preserve });
                para.AppendChild(spaceRun);

                // English run with Arial
                para.AppendChild(MakeEnglishRun(engText, srcRunProps));
            }
            else
            {
                // Normal paragraph: insert new paragraph after
                var newPara = MakeEnglishParagraph(para, engText);
                insertAfter.Add(new KeyValuePair<Paragraph, Paragraph>(para, newPara));
            }
        }
        unitIdx++;
    }

    // Insert new paragraphs after their source (iterate in reverse to preserve indices)
    foreach (var pair in insertAfter.AsEnumerable().Reverse())
    {
        pair.Key.InsertAfterSelf(pair.Value);
    }

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

                if (!skip && transMap.ContainsKey(unitIdx))
                {
                    string engText = transMap[unitIdx];
                    // Get last paragraph in cell
                    var cellParas = cell.Elements<Paragraph>().ToList();
                    if (cellParas.Count > 0)
                    {
                        var lastPara = cellParas[cellParas.Count - 1];
                        var srcRun = lastPara.Elements<Run>().FirstOrDefault();
                        var srcRunProps = srcRun?.RunProperties;

                        // Line break run
                        var brRun = new Run();
                        brRun.AppendChild(new Break());

                        lastPara.AppendChild(brRun);
                        lastPara.AppendChild(MakeEnglishRun(engText, srcRunProps));
                    }
                }
                unitIdx++;
            }
        }
    }

    doc.Save();
}

Console.WriteLine($"Bilingual DOCX saved to {outputPath}");

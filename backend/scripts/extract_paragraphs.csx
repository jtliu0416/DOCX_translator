#r "nuget: DocumentFormat.OpenXml, 3.2.0"
#r "nuget: System.Text.Json, 8.0.0"

using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;

// --- Parse CLI args ---
string inputPath = "";
string outputPath = "";

for (int i = 0; i < Args.Count; i++)
{
    if (Args[i] == "--input" && i + 1 < Args.Count) inputPath = Args[++i];
    if (Args[i] == "--output" && i + 1 < Args.Count) outputPath = Args[++i];
}

if (string.IsNullOrEmpty(inputPath) || string.IsNullOrEmpty(outputPath))
{
    Console.WriteLine("Usage: extract_paragraphs.csx --input <docx> --output <json>");
    Environment.Exit(1);
}

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
    {
        foreach (var t in run.Elements<Text>())
        {
            result += t.Text;
        }
    }
    return result.Trim();
}

static string GetCellText(TableCell cell)
{
    string result = "";
    foreach (var p in cell.Elements<Paragraph>())
    {
        result += GetText(p);
    }
    return result.Trim();
}

// --- Main ---
var units = new List<Dictionary<string, object>>();
int idx = 0;

using (var doc = WordprocessingDocument.Open(inputPath, false))
{
    var body = doc.MainDocumentPart.Document.Body;

    // Process body-level paragraphs
    foreach (var para in body.Elements<Paragraph>())
    {
        string text = GetText(para);
        if (string.IsNullOrEmpty(text)) continue;

        var pPr = para.ParagraphProperties;
        string styleId = pPr?.ParagraphStyleId?.Val?.Value ?? "Normal";
        bool isHeading = styleId.StartsWith("Heading", StringComparison.OrdinalIgnoreCase)
                      || styleId.StartsWith("heading", StringComparison.OrdinalIgnoreCase);
        int level = 0;
        if (isHeading)
        {
            var m = Regex.Match(styleId, @"\d+");
            level = m.Success ? int.Parse(m.Value) : 1;
        }

        bool hasZh = ContainsChinese(text);
        double enRatio = EnglishRatio(text);
        bool skip = !hasZh || (enRatio > 0.3 && hasZh);

        var unit = new Dictionary<string, object>
        {
            ["index"] = idx,
            ["type"] = isHeading ? "heading" : "paragraph",
            ["level"] = level,
            ["text"] = text,
            ["style_id"] = styleId,
            ["contains_chinese"] = hasZh,
            ["already_translated"] = enRatio > 0.3 && hasZh,
            ["skip"] = skip
        };
        units.Add(unit);
        idx++;
    }

    // Process tables
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

                    var unit = new Dictionary<string, object>
                    {
                        ["index"] = idx,
                        ["type"] = "table_cell",
                        ["table_index"] = tIdx,
                        ["row_index"] = rIdx,
                        ["col_index"] = cIdx,
                        ["text"] = text,
                        ["contains_chinese"] = hasZh,
                        ["already_translated"] = enRatio > 0.3 && hasZh,
                        ["skip"] = skip
                    };
                    units.Add(unit);
                    idx++;
                }
                cIdx++;
            }
            rIdx++;
        }
        tIdx++;
    }
}

// Output JSON
var result = new Dictionary<string, object>
{
    ["units"] = units
};

var options = new JsonSerializerOptions { WriteIndented = true };
string json = JsonSerializer.Serialize(result, options);
File.WriteAllText(outputPath, json);
Console.WriteLine($"Extracted {units.Count} units to {outputPath}");

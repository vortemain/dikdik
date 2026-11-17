import { expect, test } from "bun:test";
import { columnName, numericColumns } from "../../dikdik/web/spreadsheet";

test("column letters follow spreadsheet naming", () => {
  expect([0, 1, 25, 26, 27, 51, 52, 701, 702].map(columnName)).toEqual(["A", "B", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA"]);
});

test("columns that are mostly numbers align right; text, dates and blanks do not", () => {
  const rows = [
    ["1", "Nurse", "3.75", "2026-11-06", "", "12%"],
    ["2", "Clinical Officer", "4", "2026-11-07", "", "1,250"],
    ["3", "Nurse", "n/a", "2026-11-08", "", "-0.5"],
    ["4", "Lab", "2.5", "2026-11-09", "", "7"],
    ["5", "Nurse", "5", "2026-11-10", "", "1e3"],
  ];
  expect(numericColumns(rows, 6)).toEqual([true, false, true, false, false, true]);
});

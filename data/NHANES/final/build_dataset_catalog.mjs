import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(".");
const dataDir = path.join(root, "deployment_dataset");
const outputDir = path.join(root, "outputs", "diabetes_deployment_dataset");
const manifest = JSON.parse(await fs.readFile(path.join(dataDir, "manifest.json"), "utf8"));
const quality = JSON.parse(await fs.readFile(path.join(dataDir, "quality_report.json"), "utf8"));

const tableDescriptions = {
  patient_profiles: "Accepted NHANES patient profile baseline with a labelled synthetic demo name",
  patient_baseline_clinical: "Cross-sectional laboratory, vital and condition baseline",
  patient_lifestyle_baseline: "Cross-sectional lifestyle survey baseline",
  patient_prescription_status: "One observed NHANES prescription-use response per patient",
  patient_medications: "Named baseline prescription records only; no inferred dose or schedule",
  project_diabetes_cohort: "Project-eligible prediabetes and Type 2 algorithm labels with auditable evidence; Type 1 excluded",
  caregiver_profiles: "Synthetic demo caregiver for each patient aged under 16 or over 70",
  doctor_profiles: "Exactly 500 synthetic demo doctor profiles; no real credentials or contact data",
  patient_doctor_assignments: "One deterministic synthetic primary-doctor assignment per deployment patient",
};

const primaryKeys = {
  patient_profiles: "patient_id", patient_baseline_clinical: "patient_id",
  patient_lifestyle_baseline: "patient_id", patient_prescription_status: "patient_id",
  patient_medications: "medication_record_id",
  project_diabetes_cohort: "patient_id",
  caregiver_profiles: "caregiver_id",
  doctor_profiles: "doctor_id",
  patient_doctor_assignments: "assignment_id",
};

const sourceClass = (name) => ["doctor_profiles", "patient_doctor_assignments"].includes(name)
  ? "SYNTHETIC DEMO"
  : name === "caregiver_profiles"
  ? "SYNTHETIC DEMO IDENTITY"
  : name === "patient_profiles"
    ? "NHANES BASELINE + SYNTHETIC NAME"
    : "NHANES BASELINE";

const wb = Workbook.create();
const overview = wb.worksheets.add("README");
const tables = wb.worksheets.add("Tables");
const fields = wb.worksheets.add("Data Dictionary");
const qc = wb.worksheets.add("Quality Checks");

overview.getRange("A1:F1").merge();
overview.getRange("A1").values = [["Diabetes Project Deployment Dataset"]];
overview.getRange("A3:B12").values = [
  ["Dataset version", manifest.dataset_version], ["Generated at UTC", manifest.generated_at_utc],
  ["Source", manifest.source], ["Scope", manifest.scope], ["Synthetic clinical data", "No"],
  ["Synthetic identity data", "Yes - explicitly labelled"],
  ["Key rule", "Treat all records as cross-sectional baseline observations"],
  ["Medication rule", "Do not infer dose or schedule from NHANES"],
  ["Label rule", "Deployment contains PREDIABETES and algorithm-classified TYPE_2 only; TYPE_1 is excluded"],
  ["Encoding", "CSV UTF-8 with BOM"],
];
for (let row = 3; row <= 12; row++) overview.getRange(`B${row}:F${row}`).merge();
overview.getRange("A14:F14").merge();
overview.getRange("A14").values = [["Known limitations"]];
overview.getRange(`A15:F${14 + manifest.limitations.length}`).merge(true);
overview.getRange(`A15:A${14 + manifest.limitations.length}`).values = manifest.limitations.map(x => [x]);

const tableRows = Object.entries(manifest.tables).map(([name, rows]) => [
  name, `${name}.csv`, rows, primaryKeys[name], sourceClass(name), tableDescriptions[name]
]);
tables.getRange(`A1:F${tableRows.length + 1}`).values = [["Table", "File", "Rows", "Primary key", "Source class", "Purpose"], ...tableRows];
tables.tables.add(`A1:F${tableRows.length + 1}`, true, "DeploymentTables");

const fieldRows = [];
for (const name of Object.keys(manifest.tables)) {
  const csv = await fs.readFile(path.join(dataDir, `${name}.csv`), "utf8");
  const header = csv.split(/\r?\n/, 1)[0].replace(/^\uFEFF/, "").split(",");
  for (const field of header) {
    let role = field === primaryKeys[name] ? "PRIMARY KEY" : "ATTRIBUTE";
    if (["patient_id", "doctor_id", "demo_patient_id", "glucose_record_id"].includes(field)) role = role === "PRIMARY KEY" ? role : "FOREIGN KEY / LINK";
    let unit = "";
    if (field.endsWith("_mg_dl")) unit = "mg/dL";
    else if (field.endsWith("_pct")) unit = "%";
    else if (field.endsWith("_kg")) unit = "kg";
    else if (field.endsWith("_cm")) unit = "cm";
    else if (field.endsWith("_minutes")) unit = "minutes";
    const synthetic = ["doctor_profiles", "patient_doctor_assignments"].includes(name)
      ? "Yes - full demo record"
      : name === "caregiver_profiles" ||
      (name === "patient_profiles" && ["display_name", "is_synthetic_identity", "identity_source"].includes(field))
      ? "Yes - identity only" : "No";
    fieldRows.push([name, field, role, unit, sourceClass(name), synthetic]);
  }
}
fields.getRange(`A1:F${fieldRows.length + 1}`).values = [["Table", "Field", "Role", "Unit", "Source class", "Synthetic"], ...fieldRows];
fields.tables.add(`A1:F${fieldRows.length + 1}`, true, "FieldDictionary");

const qcRows = [];
const addQualityRows = (value, path = []) => {
  if (value !== null && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) addQualityRows(child, [...path, key]);
    return;
  }
  const metric = path.at(-1);
  const scope = path.slice(0, -1).join(".") || "GLOBAL";
  const status = metric === "status" ? String(value) :
    (typeof value === "number" && value === 0 ? "PASS" : "INFO");
  qcRows.push([scope, metric, Array.isArray(value) ? value.join(", ") : value, status]);
};
addQualityRows(quality);
qc.getRange(`A1:D${qcRows.length + 1}`).values = [["Scope", "Check", "Result", "Status"], ...qcRows];
qc.tables.add(`A1:D${qcRows.length + 1}`, true, "QualityChecks");

for (const sheet of [overview, tables, fields, qc]) {
  sheet.showGridlines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.getRange("A1:F1").format = { fill: "#0F766E", font: { bold: true, color: "#FFFFFF", size: 12 } };
  sheet.getUsedRange().format.verticalAlignment = "center";
  sheet.getUsedRange().format.wrapText = true;
  sheet.getUsedRange().format.autofitColumns();
  sheet.getUsedRange().format.autofitRows();
}
overview.getRange("A1:F1").format = { fill: "#0F766E", font: { bold: true, color: "#FFFFFF", size: 18 }, horizontalAlignment: "center" };
overview.getRange("A14:F14").format = { fill: "#CCFBF1", font: { bold: true, color: "#134E4A" } };
overview.getRange("A:A").format.columnWidth = 28;
overview.getRange("B:F").format.columnWidth = 18;
overview.getRange("3:12").format.rowHeight = 32;
overview.getRange("B4:F4").format.numberFormat = "yyyy-mm-dd hh:mm:ss";
tables.getRange("A:F").format.columnWidth = 24;
tables.getRange("F:F").format.columnWidth = 44;
tables.getRange(`2:${tableRows.length + 1}`).format.rowHeight = 30;
fields.getRange("A:D").format.columnWidth = 24;
fields.getRange("E:E").format.columnWidth = 36;
fields.getRange("F:F").format.columnWidth = 22;
fields.getUsedRange().format.autofitRows();
qc.getRange("A:A").format.columnWidth = 36;
qc.getRange("B:B").format.columnWidth = 42;
qc.getRange("C:C").format.columnWidth = 18;
qc.getRange("D:D").format.columnWidth = 14;
qc.getUsedRange().format.autofitRows();
qc.getRange(`D2:D${qcRows.length + 1}`).conditionalFormats.add("containsText", { text: "PASS", format: { fill: "#DCFCE7", font: { color: "#166534", bold: true } } });
qc.getRange(`D2:D${qcRows.length + 1}`).conditionalFormats.add("containsText", { text: "REVIEW", format: { fill: "#FEF3C7", font: { color: "#92400E", bold: true } } });

await fs.mkdir(outputDir, { recursive: true });
for (const sheetName of ["README", "Tables", "Data Dictionary", "Quality Checks"]) {
  const preview = await wb.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, `${sheetName.replaceAll(" ", "_")}.png`), new Uint8Array(await preview.arrayBuffer()));
}
const out = await SpreadsheetFile.exportXlsx(wb);
await out.save(path.join(outputDir, "NHANES_Diabetes_Project_Data_Catalog.xlsx"));

const inspection = await wb.inspect({ kind: "table", range: "Quality Checks!A1:D20", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 6 });
console.log(inspection.ndjson);
const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
console.log(errors.ndjson);

// Keep the student entries and subject list in the page so the whole tool works offline.
const subjectsContainer = document.querySelector("#subjects");
const studentsContainer = document.querySelector("#student-entry");
const subjectCountLabel = document.querySelector("#subject-count");
const resultsPanel = document.querySelector("#results");
const message = document.querySelector("#form-message");
const studentListBody = document.querySelector("#student-list");
const studentListMessage = document.querySelector("#student-list-message");
const studentForm = document.querySelector("#student-form");
const studentFormError = document.querySelector("#student-form-error");
const toast = document.querySelector("#toast");
const studentDialog = document.querySelector("#student-dialog");
let savedStudents = [];
let searchTimer = null;
let toastTimer = null;

// Give each subject a stable ID so adding, removing, or renaming it won't mix up marks.
let subjectNames = [
  { id: "subject-1", name: "Maths" },
  { id: "subject-2", name: "Science" },
  { id: "subject-3", name: "English" },
];
let nextSubjectId = 4;
let studentRows = 1;
let latestResults = null;
let academicYears = [];
let schoolClasses = [];
let schoolSections = [];

function renderSubjects() {
  subjectsContainer.innerHTML = "";
  subjectNames.forEach((subject, index) => {
    const row = document.createElement("div");
    row.className = "subject-row";
    row.innerHTML = `<span class="subject-index">${String(index + 1).padStart(2, "0")}</span>
      <input type="text" aria-label="Subject ${index + 1} name" value="${escapeAttribute(subject.name)}" placeholder="Subject name" required>
      ${subjectNames.length > 1 ? '<button class="icon-button remove-subject" type="button" aria-label="Remove subject">×</button>' : ""}`;
    row.querySelector("input").addEventListener("input", (event) => {
      subjectNames[index].name = event.target.value;
      updateStudentSubjectLabels();
    });
    row.querySelector(".remove-subject")?.addEventListener("click", () => {
      subjectNames.splice(index, 1);
      renderSubjects();
      updateStudentSubjectLabels();
    });
    subjectsContainer.append(row);
  });
  subjectCountLabel.textContent = `${subjectNames.length} ${subjectNames.length === 1 ? "subject" : "subjects"}`;
}

function renderStudentRow(number) {
  const row = document.createElement("div");
  row.className = "student-row";
  row.dataset.student = number;
  row.innerHTML = `<div class="student-row-top">
      <span class="row-number">${String(number).padStart(2, "0")}</span>
      <select class="student-name student-select" aria-label="Student ${number}" required></select>
      ${number > 1 ? '<button class="icon-button remove-student" type="button" aria-label="Remove student">×</button>' : ""}
    </div>
    <div class="marks-grid"></div>`;
  row.querySelector(".remove-student")?.addEventListener("click", () => {
    row.remove();
    renumberStudents();
  });
  studentsContainer.append(row);
  refreshStudentSelectors();
  updateStudentSubjectLabels();
}

function refreshStudentSelectors() {
  document.querySelectorAll(".student-select").forEach((select) => {
    const selectedId = select.value;
    select.replaceChildren(new Option("Choose a saved student", ""));
    savedStudents.forEach((student) => {
      const label = `${student.name} · ${student.student_code}`;
      select.add(new Option(label, String(student.id)));
    });
    if (savedStudents.some((student) => String(student.id) === selectedId)) {
      select.value = selectedId;
    }
  });
}

function updateStudentSubjectLabels() {
  document.querySelectorAll(".student-row").forEach((row) => {
    const grid = row.querySelector(".marks-grid");
    // Save each value under its subject ID before rebuilding the fields and labels.
    const savedMarks = new Map(
      [...grid.querySelectorAll(".mark-input")].map((input) => [input.dataset.subjectId, input.value]),
    );
    grid.innerHTML = "";
    subjectNames.forEach((subject, index) => {
      const cell = document.createElement("div");
      cell.className = "mark-cell";
      const fieldId = `mark-${row.dataset.student}-${subject.id}`;
      const subjectName = subject.name.trim() || `Subject ${index + 1}`;
      cell.dataset.subjectId = subject.id;
      cell.innerHTML = `<label for="${fieldId}">${escapeHtml(subjectName)}</label>
        <input class="mark-input" data-subject-id="${subject.id}" id="${fieldId}" type="number" min="0" max="100" step="0.01" placeholder="0–100" required aria-label="${escapeAttribute(subjectName)} marks">`;
      cell.querySelector(".mark-input").value = savedMarks.get(subject.id) ?? "";
      grid.append(cell);
    });
  });
}

function renumberStudents() {
  document.querySelectorAll(".student-row").forEach((row, index) => {
    row.dataset.student = index + 1;
    row.querySelector(".row-number").textContent = String(index + 1).padStart(2, "0");
    row.querySelector(".student-name").setAttribute("aria-label", `Student ${index + 1}`);
    const removeButton = row.querySelector(".remove-student");
    if (index === 0 && removeButton) removeButton.remove();
    if (index > 0 && !removeButton) {
      const button = document.createElement("button");
      button.className = "icon-button remove-student";
      button.type = "button";
      button.setAttribute("aria-label", "Remove student");
      button.textContent = "×";
      button.addEventListener("click", () => { row.remove(); renumberStudents(); });
      row.querySelector(".student-row-top").append(button);
    }
  });
  updateStudentSubjectLabels();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

function formatMark(value) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

async function analyzeMarks(event) {
  event.preventDefault();
  message.textContent = "";
  const passMark = Number(document.querySelector("#pass-mark").value);
  const cleanSubjects = subjectNames.map((subject) => subject.name.trim());
  if (!cleanSubjects.length || cleanSubjects.some((subject) => !subject)) {
    message.textContent = "Please enter a name for every subject.";
    return;
  }
  if (new Set(cleanSubjects.map((subject) => subject.toLocaleLowerCase())).size !== cleanSubjects.length) {
    message.textContent = "Each subject needs a different name.";
    return;
  }

  if (!Number.isFinite(passMark) || passMark < 0 || passMark > 100) {
    message.textContent = "The pass mark must be between 0 and 100.";
    return;
  }
  const rows = [...document.querySelectorAll(".student-row")];
  const selectedIds = rows.map((row) => Number(row.querySelector(".student-select").value));
  if (selectedIds.some((studentId) => !Number.isInteger(studentId) || studentId < 1)) {
    message.textContent = "Choose a saved student in every row before analyzing marks.";
    return;
  }
  if (new Set(selectedIds).size !== selectedIds.length) {
    message.textContent = "Choose each student only once.";
    return;
  }
  const students = rows.map((row, index) => {
    const marks = [...row.querySelectorAll(".mark-input")].map((input) => input.value.trim());
    return { id: selectedIds[index], marks: marks.map(Number) };
  });
  const hasMissingMark = rows.some((row) =>
    [...row.querySelectorAll(".mark-input")].some((input) => input.value.trim() === ""),
  );
  if (hasMissingMark || students.some((student) =>
    student.marks.some((mark) => !Number.isFinite(mark) || mark < 0 || mark > 100))) {
    message.textContent = "Enter a number from 0 to 100 for every subject mark.";
    return;
  }

  const submitButton = document.querySelector("#analyzer-form .primary-button");
  submitButton.disabled = true;
  submitButton.textContent = "Saving and analyzing…";
  try {
    const response = await fetchJson("/api/analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passMark, subjects: cleanSubjects, students }),
    });
    latestResults = response.analysis;
    renderResults(latestResults);
    await loadDashboard();
    showToast("Marks saved to the Markwise database.");
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submitButton.disabled = false;
    submitButton.innerHTML = 'Analyze marks <span aria-hidden="true">→</span>';
  }
}

function renderResults(data) {
  const subjectHeaders = data.subjects.map((subject) => `<th scope="col">${escapeHtml(subject)}</th>`).join("");
  const studentRowsHtml = data.students.map((student) => {
    const highest = data.highestStudentIds.includes(student.id);
    const subjectCells = student.marks.map((mark) => `<td>${formatMark(mark)}</td>`).join("");
    return `<tr><td>${escapeHtml(student.name)}${highest ? '<span class="highest-label">★ Top</span>' : ""}</td>
      ${subjectCells}<td><strong>${formatMark(student.average)}</strong></td>
      <td><span class="status ${student.status.toLowerCase()}">${student.status}</span></td></tr>`;
  }).join("");
  resultsPanel.innerHTML = `<div class="results-content">
    <p class="eyebrow">YOUR CLASS OVERVIEW</p><h2>Results are ready</h2>
    <p class="results-subtitle">${data.exam} · ${data.studentCount} students · ${data.subjects.length} subjects · pass mark ${formatMark(data.passMark)}</p>
    <div class="summary-grid">
      <div class="summary-card"><span>Class average</span><strong>${formatMark(data.classAverage)}</strong><small>Across selected students</small></div>
      <div class="summary-card"><span>Students</span><strong>${data.studentCount}</strong><small>${data.students.filter((student) => student.status === "Pass").length} passed · ${data.students.filter((student) => student.status === "Fail").length} failed</small></div>
      <div class="summary-card top-student"><span>Highest average</span><strong>${formatMark(data.highestAverage)}</strong><small>${data.highestNames.map(escapeHtml).join(", ")}</small></div>
      <div class="summary-card"><span>Pass mark</span><strong>${formatMark(data.passMark)}</strong><small>Average needed to pass</small></div>
    </div>
    <div class="results-bar"><h3>Student results</h3><button id="download-csv" class="download-button" type="button">↓ Download CSV</button></div>
    <div class="table-wrap"><table class="results-table"><thead><tr><th scope="col">Student</th>${subjectHeaders}<th scope="col">Average</th><th scope="col">Result</th></tr></thead><tbody>${studentRowsHtml}</tbody></table></div>
    <p class="results-footnote">Sorted by average, highest first. A student passes when their average across subjects meets or exceeds the pass mark. Saved results remain after a restart.</p>
  </div>`;
  document.querySelector("#download-csv").addEventListener("click", downloadCsv);
}

function downloadCsv() {
  if (!latestResults) return;
  const { students, subjects, passMark } = latestResults;
  const lines = [["Student", ...subjects, "Average", "Result"], ...students.map((student) => [
    student.name, ...student.marks, student.average.toFixed(2), student.average >= passMark ? "Pass" : "Fail",
  ])];
  const csv = lines.map((line) => line.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
  link.download = "student_marks_results.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}

async function fetchJson(url, options = {}) {
  // Codex's static file preview has no Flask process behind its file:// origin.
  // Keep API calls same-origin on the Flask-served page instead of weakening CORS.
  if (window.location.protocol === "file:") {
    throw new Error("This file preview cannot connect to Markwise storage. Run Flask and open http://127.0.0.1:5000/ in your browser.");
  }
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error("The server returned an unreadable response.");
  }
  if (!response.ok) throw new Error(payload.error || "The request could not be completed.");
  return payload;
}

async function refreshStudents() {
  studentListMessage.textContent = "Loading students…";
  try {
    const response = await fetchJson("/api/students");
    savedStudents = response.students;
    refreshStudentSelectors();
    await refreshSchoolStructure();
    await searchStudents();
    await loadLatestAnalysis();
  } catch (error) {
    studentListMessage.textContent = error.message;
  }
}

async function loadLatestAnalysis() {
  try {
    const response = await fetchJson("/api/analysis/latest");
    if (!response.analysis) return;
    latestResults = response.analysis;
    document.querySelector("#pass-mark").value = latestResults.passMark;
    subjectNames = latestResults.subjects.map((name, index) => ({ id: `saved-subject-${index + 1}`, name }));
    nextSubjectId = subjectNames.length + 1;
    renderSubjects();
    studentsContainer.innerHTML = "";
    studentRows = 0;
    latestResults.students.forEach((student) => {
      studentRows += 1;
      renderStudentRow(studentRows);
      const row = studentsContainer.lastElementChild;
      row.querySelector(".student-select").value = String(student.id);
      row.querySelectorAll(".mark-input").forEach((input, index) => {
        input.value = student.marks[index];
      });
    });
    renderResults(latestResults);
  } catch (_error) {
    // Student CRUD remains available even if the last saved analysis is unavailable.
  }
}

async function searchStudents() {
  const query = document.querySelector("#student-search").value.trim();
  studentListMessage.textContent = "Searching…";
  try {
    const params = new URLSearchParams({ search: query });
    const year = document.querySelector("#student-year-filter").value;
    const classId = document.querySelector("#student-class-filter").value;
    const sectionId = document.querySelector("#student-section-filter").value;
    const status = document.querySelector("#student-status-filter").value;
    if (year) params.set("academicYearId", year);
    if (classId) params.set("classId", classId);
    if (sectionId) params.set("sectionId", sectionId);
    if (status) params.set("status", status);
    const response = await fetchJson(`/api/students?${params}`);
    renderStudentList(response.students);
    studentListMessage.textContent = response.total
      ? `${response.total} ${response.total === 1 ? "student" : "students"}`
      : "No students found. Add a student to begin.";
  } catch (error) {
    studentListBody.innerHTML = "";
    studentListMessage.textContent = error.message;
  }
}

function renderStudentList(students) {
  studentListBody.innerHTML = students.map((student) => `<tr>
    <td>${escapeHtml(student.name)}</td>
    <td>${escapeHtml(student.student_code)}</td>
    <td>${escapeHtml(student.admission_number || "—")}</td>
    <td>${escapeHtml(student.class_name || "—")}</td>
    <td>${escapeHtml(student.section_name || "—")}</td>
    <td>${escapeHtml(student.roll_number || "—")}</td>
    <td>${escapeHtml(student.enrollment_status || "active")}</td>
    <td class="student-actions">
      <button type="button" class="table-action" data-action="view" data-id="${student.id}">View</button>
      <button type="button" class="table-action" data-action="edit" data-id="${student.id}">Edit</button>
      <button type="button" class="table-action delete-action" data-action="delete" data-id="${student.id}">Delete</button>
    </td>
  </tr>`).join("");
}

function startNewStudent() {
  studentForm.reset();
  document.querySelector("#student-record-id").value = "";
  document.querySelector("#student-form-title").textContent = "Add a student";
  studentFormError.textContent = "";
  studentForm.hidden = false;
  document.querySelector("#student-name").focus();
}

async function startStudentEdit(studentId) {
  try {
    const response = await fetchJson(`/api/students/${studentId}`);
    const student = response.student;
    document.querySelector("#student-record-id").value = student.id;
    document.querySelector("#student-name").value = student.name;
    document.querySelector("#student-code").value = student.student_code;
    document.querySelector("#student-admission").value = student.admission_number || "";
    document.querySelector("#student-roll").value = student.roll_number || "";
    document.querySelector("#student-class").value = student.class_name || "";
    document.querySelector("#student-section").value = student.section_name || "";
    document.querySelector("#guardian-name").value = student.guardian_name || "";
    document.querySelector("#guardian-contact").value = student.guardian_contact || "";
    document.querySelector("#admission-date").value = student.admission_date || "";
    document.querySelector("#enrollment-status").value = student.enrollment_status || "active";
    document.querySelector("#student-form-title").textContent = "Edit student";
    studentFormError.textContent = "";
    studentForm.hidden = false;
    document.querySelector("#student-name").focus();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function showStudentDetails(studentId) {
  try {
    const response = await fetchJson(`/api/students/${studentId}`);
    const student = response.student;
    const marksTable = student.marks.length
      ? `<div class="table-wrap"><table class="results-table"><thead><tr><th>Exam</th><th>Subject</th><th>Marks</th><th>Maximum</th></tr></thead><tbody>${student.marks.map((mark) => `<tr><td>${escapeHtml(mark.exam_name)}</td><td>${escapeHtml(mark.subject_name)}</td><td>${mark.score === null ? "Not entered" : formatMark(mark.score)}</td><td>${formatMark(mark.maximum_marks)}</td></tr>`).join("")}</tbody></table></div>`
      : '<p class="detail-empty">No saved marks yet.</p>';
    document.querySelector("#student-details").innerHTML = `<p class="eyebrow">STUDENT RECORD</p>
      <h2 id="student-dialog-title">${escapeHtml(student.name)}</h2>
      <dl class="student-detail-grid">
        <div><dt>Student ID</dt><dd>${escapeHtml(student.student_code)}</dd></div>
        <div><dt>Admission number</dt><dd>${escapeHtml(student.admission_number || "—")}</dd></div>
        <div><dt>Roll number</dt><dd>${escapeHtml(student.roll_number || "—")}</dd></div>
        <div><dt>Class</dt><dd>${escapeHtml(student.class_name || "—")}</dd></div>
        <div><dt>Section</dt><dd>${escapeHtml(student.section_name || "—")}</dd></div>
        <div><dt>Guardian</dt><dd>${escapeHtml(student.guardian_name || "—")}</dd></div>
        <div><dt>Guardian contact</dt><dd>${escapeHtml(student.guardian_contact || "—")}</dd></div>
        <div><dt>Admission date</dt><dd>${escapeHtml(student.admission_date || "—")}</dd></div>
        <div><dt>Enrollment status</dt><dd>${escapeHtml(student.enrollment_status || "active")}</dd></div>
      </dl><h3>Saved marks</h3>${marksTable}`;
    const enrollments = student.enrollments.length
      ? `<ul class="plain-list">${student.enrollments.map((entry) => `<li><strong>${escapeHtml(entry.academic_year)}</strong> · ${escapeHtml(entry.class_name)}${entry.section_name ? ` / ${escapeHtml(entry.section_name)}` : ""} · Roll ${escapeHtml(entry.roll_number || "—")} · ${escapeHtml(entry.status)}</li>`).join("")}</ul>`
      : '<p class="detail-empty">No academic-year enrollments yet.</p>';
    document.querySelector("#student-details").insertAdjacentHTML("beforeend", `<h3>Enrollment history</h3>${enrollments}
      <form id="enrollment-form" class="compact-form enrollment-form"><h3>Add or update enrollment</h3>
        <label>Academic year<select name="academicYearId" required>${academicYears.map((year) => `<option value="${year.id}" ${year.is_current ? "selected" : ""}>${escapeHtml(year.label)}${year.is_current ? " · Current" : ""}</option>`).join("")}</select></label>
        <label>Class<select name="classId" required>${schoolClasses.map((record) => `<option value="${record.id}">${escapeHtml(record.name)}</option>`).join("")}</select></label>
        <label>Section<select name="sectionId"><option value="">No section</option>${schoolSections.map((section) => `<option value="${section.id}" data-class="${section.class_id}">${escapeHtml(section.class_name)} · ${escapeHtml(section.name)}</option>`).join("")}</select></label>
        <label>Roll number<input name="rollNumber" maxlength="40" value="${escapeAttribute(student.roll_number || "")}"></label>
        <label>Status<select name="status"><option value="active">Active</option><option value="completed">Completed</option><option value="transferred">Transferred</option></select></label>
        <p class="form-message" role="alert"></p><button class="secondary-button" type="submit">Save enrollment</button>
      </form>`);
    wireEnrollmentForm(student.id);
    studentDialog.showModal();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function removeStudent(studentId, studentName) {
  const confirmed = window.confirm(
    `Remove ${studentName} from the active student list? Saved marks will be retained for school history.`,
  );
  if (!confirmed) return;
  try {
    const result = await fetchJson(`/api/students/${studentId}`, { method: "DELETE" });
    await refreshStudents();
    await loadDashboard();
    showToast(result.message);
  } catch (error) {
    showToast(error.message, true);
  }
}

function showToast(text, isError = false) {
  toast.textContent = text;
  toast.classList.toggle("toast-error", isError);
  toast.classList.add("toast-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("toast-visible"), 3500);
}

async function saveStudent(event) {
  event.preventDefault();
  studentFormError.textContent = "";
  const studentId = document.querySelector("#student-record-id").value;
  const payload = {
    name: document.querySelector("#student-name").value.trim(),
    studentCode: document.querySelector("#student-code").value.trim(),
    admissionNumber: document.querySelector("#student-admission").value.trim(),
    rollNumber: document.querySelector("#student-roll").value.trim(),
    className: document.querySelector("#student-class").value.trim(),
    sectionName: document.querySelector("#student-section").value.trim(),
    guardianName: document.querySelector("#guardian-name").value.trim(),
    guardianContact: document.querySelector("#guardian-contact").value.trim(),
    admissionDate: document.querySelector("#admission-date").value,
    enrollmentStatus: document.querySelector("#enrollment-status").value,
  };
  const submitButton = studentForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    const saved = await fetchJson(studentId ? `/api/students/${studentId}` : "/api/students", {
      method: studentId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    studentForm.reset();
    studentForm.hidden = true;
    await refreshStudents();
    await loadDashboard();
    showToast(studentId ? "Student record updated." : `Student added: ${saved.student.student_code}`);
  } catch (error) {
    studentFormError.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
}

async function refreshSchoolStructure() {
  try {
    const [yearsResponse, classesResponse, sectionsResponse] = await Promise.all([
      fetchJson("/api/academic-years"), fetchJson("/api/classes"), fetchJson("/api/sections"),
    ]);
    academicYears = yearsResponse.academicYears;
    schoolClasses = classesResponse.classes;
    schoolSections = sectionsResponse.sections;
    document.querySelector("#academic-year-list").innerHTML = academicYears.map((year) =>
      `<li><strong>${escapeHtml(year.label)}</strong>${year.is_current ? '<span class="current-chip">Current</span>' : ""}<small>${escapeHtml(year.starts_on || "Date not set")} – ${escapeHtml(year.ends_on || "Date not set")}</small></li>`).join("") || '<li class="detail-empty">No academic years yet.</li>';
    document.querySelector("#class-list").innerHTML = schoolClasses.map((record) => {
      const sections = schoolSections.filter((section) => section.class_id === record.id);
      return `<article class="class-item"><strong>${escapeHtml(record.name)}</strong><span>${record.active_students} enrolled · ${record.section_count} sections</span><small>${sections.map((section) => escapeHtml(section.name)).join(" · ") || "No sections"}</small></article>`;
    }).join("") || '<p class="detail-empty">No classes yet.</p>';
    fillSelect("#student-year-filter", academicYears, "Current year", (year) => `${year.label}${year.is_current ? " · Current" : ""}`);
    fillSelect("#student-class-filter", schoolClasses, "All classes", (record) => record.name);
    fillSelect("#section-form select[name=classId]", schoolClasses, "Choose class", (record) => record.name);
    const sectionFilter = document.querySelector("#student-section-filter");
    const selectedSection = sectionFilter.value;
    sectionFilter.innerHTML = '<option value="">All sections</option>' + schoolSections.map((section) => `<option value="${section.id}" data-class="${section.class_id}">${escapeHtml(section.class_name)} · ${escapeHtml(section.name)}</option>`).join("");
    if (schoolSections.some((section) => String(section.id) === selectedSection)) sectionFilter.value = selectedSection;
  } catch (error) {
    showToast(`School structure could not load: ${error.message}`, true);
  }
}

function fillSelect(selector, records, firstLabel, labelFor) {
  const select = document.querySelector(selector);
  const previous = select.value;
  select.innerHTML = `<option value="">${escapeHtml(firstLabel)}</option>` + records.map((record) => `<option value="${record.id}">${escapeHtml(labelFor(record))}</option>`).join("");
  if (records.some((record) => String(record.id) === previous)) select.value = previous;
}

function wireEnrollmentForm(studentId) {
  const form = document.querySelector("#enrollment-form");
  const classSelect = form.elements.classId;
  const sectionSelect = form.elements.sectionId;
  const updateSections = () => {
    const classId = classSelect.value;
    [...sectionSelect.options].forEach((option) => {
      if (option.value) option.hidden = option.dataset.class !== classId;
    });
    if (sectionSelect.selectedOptions[0]?.dataset.class !== classId) sectionSelect.value = "";
  };
  classSelect.addEventListener("change", updateSections);
  updateSections();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const errorArea = form.querySelector("[role=alert]");
    button.disabled = true;
    errorArea.textContent = "";
    try {
      await fetchJson(`/api/students/${studentId}/enrollments`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      studentDialog.close();
      await refreshStudents();
      await loadDashboard();
      showToast("Enrollment saved. Previous academic years were retained.");
    } catch (error) {
      errorArea.textContent = error.message;
    } finally { button.disabled = false; }
  });
}

async function submitStructureForm(form, endpoint, body, errorSelector, successMessage) {
  const errorArea = document.querySelector(errorSelector);
  errorArea.textContent = "";
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await fetchJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    form.reset();
    await refreshSchoolStructure();
    await searchStudents();
    await loadDashboard();
    showToast(successMessage);
  } catch (error) { errorArea.textContent = error.message; }
  finally { button.disabled = false; }
}

function fillDashboardFilter(select, records, firstLabel, labelFor, selectedValue) {
  select.innerHTML = `<option value="">${escapeHtml(firstLabel)}</option>` + records.map((record) =>
    `<option value="${record.id}">${escapeHtml(labelFor(record))}</option>`).join("");
  if (records.some((record) => String(record.id) === String(selectedValue))) select.value = String(selectedValue);
}

function renderDashboard(data) {
  const summary = data.summary;
  document.querySelector("#metric-students").textContent = summary.totalActiveStudents;
  document.querySelector("#metric-students-note").textContent = `${summary.studentsWithMarks} with valid marks · selected roster filters`;
  document.querySelector("#metric-average").textContent = summary.classAveragePercentage === null ? "—" : `${summary.classAveragePercentage}%`;
  document.querySelector("#metric-pass-rate").textContent = summary.passPercentage === null ? "—" : `${summary.passPercentage}%`;
  document.querySelector("#metric-pass-note").textContent = summary.passMarkPercentage === null
    ? "No valid marks to compare" : `Configured average pass mark: ${summary.passMarkPercentage}%`;
  const highest = summary.highestPerformers;
  document.querySelector("#metric-highest").textContent = highest.length
    ? highest.map((student) => student.name).join(", ") : "—";
  document.querySelector("#metric-highest-note").textContent = highest.length
    ? `${highest[0].percentage}%${highest.length > 1 ? " · tied highest" : ""}` : "No valid results for this exam";

  fillDashboardFilter(document.querySelector("#dashboard-year"), data.academicYears, "Choose academic year", (year) => `${year.label}${year.is_current ? " · Current" : ""}`, data.selected.academicYearId);
  fillDashboardFilter(document.querySelector("#dashboard-class"), data.classes, "All classes", (record) => record.name, data.selected.classId);
  renderDashboardSections(data.sections, data.selected.classId, data.selected.sectionId);
  fillDashboardFilter(document.querySelector("#dashboard-exam"), data.exams, "Latest available exam", (exam) => `${exam.name}${exam.exam_date ? ` · ${exam.exam_date}` : ""}`, data.selected.examId);

  const examHasSubjects = data.subjects.length > 0;
  document.querySelector("#dashboard-empty").hidden = examHasSubjects;
  document.querySelector("#subject-chart").innerHTML = renderSubjectChart(data.subjects);
  document.querySelector("#grade-chart").innerHTML = renderGradeChart(data.gradeDistribution, summary.studentsWithMarks);
  document.querySelector("#pass-chart").innerHTML = renderPassChart(data.passFailDistribution, summary.studentsWithMarks);
  document.querySelector("#student-chart").innerHTML = renderStudentChart(data.results);
  document.querySelector("#dashboard-rules").innerHTML = Object.values(data.rules).map((rule) => `<li>${escapeHtml(rule)}</li>`).join("");

  const sortedResults = [...data.results].sort((a, b) => {
    if (a.rank !== null && b.rank !== null) return a.rank - b.rank || a.name.localeCompare(b.name);
    if (a.rank !== null) return -1;
    if (b.rank !== null) return 1;
    return a.name.localeCompare(b.name);
  });
  const body = document.querySelector("#dashboard-results");
  body.innerHTML = sortedResults.map((student) => `<tr>
    <td>${student.rank === null ? "—" : student.rank}</td>
    <td>${escapeHtml(student.name)}</td><td>${escapeHtml(student.studentCode)}</td>
    <td>${escapeHtml([student.className, student.sectionName].filter(Boolean).join(" · ") || "—")}</td>
    <td>${student.marksEntered} / ${student.subjectCount}</td>
    <td>${student.percentage === null ? "—" : `${student.percentage}%`}</td>
    <td>${student.grade || "—"}</td><td><span class="result-chip ${student.status === "Pass" ? "is-pass" : student.status === "Fail" ? "is-fail" : "is-empty"}">${escapeHtml(student.status)}</span></td>
  </tr>`).join("");
  document.querySelector("#results-count").textContent = `${data.results.length} student${data.results.length === 1 ? "" : "s"}`;
  document.querySelector("#results-empty").hidden = data.results.length > 0;
  document.querySelector("#dashboard-status").textContent = data.exams.find((exam) => exam.id === data.selected.examId)?.name || "Live saved data";
}

function renderDashboardSections(sections, classId, selectedId) {
  const filtered = classId ? sections.filter((section) => section.class_id === Number(classId)) : sections;
  fillDashboardFilter(document.querySelector("#dashboard-section"), filtered, "All sections", (section) => {
    const parent = sections.find((item) => item.id === section.id);
    return parent?.class_name ? `${parent.class_name} · ${section.name}` : section.name;
  }, selectedId);
}

function renderSubjectChart(subjects) {
  if (!subjects.some((subject) => subject.averagePercentage !== null)) return '<p class="chart-empty">No valid subject marks for this exam.</p>';
  return subjects.map((subject) => {
    const value = subject.averagePercentage;
    if (value === null) return `<div class="subject-bar-row"><span>${escapeHtml(subject.name)}</span><div class="bar-track"><i style="width:0%"></i></div><strong>—</strong><small>No marks</small></div>`;
    return `<div class="subject-bar-row"><span>${escapeHtml(subject.name)}</span><div class="bar-track"><i style="width:${Math.max(0, Math.min(100, value))}%"></i></div><strong>${value}%</strong><small>${subject.studentCount} student${subject.studentCount === 1 ? "" : "s"} · ${formatDashboardMark(subject.lowestMarks)}–${formatDashboardMark(subject.highestMarks)} / ${formatDashboardMark(subject.maximumMarks)}</small></div>`;
  }).join("");
}

function renderGradeChart(distribution, total) {
  if (!total) return '<p class="chart-empty">Grade counts appear after marks are saved.</p>';
  return `<div class="grade-bars">${Object.entries(distribution).map(([grade, count]) => {
    const height = Math.max(count ? 5 : 0, Math.round(count / total * 100));
    return `<div class="grade-column"><strong>${count}</strong><div class="grade-track"><i style="height:${height}%"></i></div><span>${grade}</span></div>`;
  }).join("")}</div><p class="chart-caption">${total} student${total === 1 ? "" : "s"} with valid results</p>`;
}

function renderPassChart(distribution, total) {
  if (!total) return '<p class="chart-empty">No pass/fail results to show yet.</p>';
  const passPercent = distribution.pass / total * 100;
  return `<div class="pass-chart-layout"><div class="donut-chart" style="--pass:${passPercent}%" aria-hidden="true"><span>${Math.round(passPercent)}%</span></div><ul class="chart-legend"><li><i class="legend-pass"></i>Pass <strong>${distribution.pass}</strong></li><li><i class="legend-fail"></i>Fail <strong>${distribution.fail}</strong></li></ul></div>`;
}

function renderStudentChart(results) {
  const leaders = results.filter((student) => student.percentage !== null)
    .sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name));
  if (!leaders.length) return '<p class="chart-empty">Student comparisons appear after marks are saved.</p>';
  return leaders.map((student) => `<div class="student-bar-row"><span title="${escapeAttribute(student.name)}">${escapeHtml(student.name)}</span><div class="bar-track"><i style="width:${Math.max(0, Math.min(100, student.percentage))}%"></i></div><strong>${student.percentage}%</strong></div>`).join("");
}

function formatDashboardMark(value) {
  return value === null ? "—" : Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)));
}

async function loadDashboard() {
  const form = document.querySelector("#dashboard-filters");
  const apply = form.querySelector('button[type="submit"]');
  const error = document.querySelector("#dashboard-error");
  const query = new URLSearchParams();
  for (const [selector, key] of [["#dashboard-year", "academicYearId"], ["#dashboard-class", "classId"], ["#dashboard-section", "sectionId"], ["#dashboard-exam", "examId"], ["#dashboard-search", "search"]]) {
    const value = document.querySelector(selector).value.trim();
    if (value) query.set(key, value);
  }
  error.textContent = "";
  apply.disabled = true;
  try {
    const data = await fetchJson(`/api/dashboard/analytics${query.size ? `?${query}` : ""}`);
    renderDashboard(data);
  } catch (problem) {
    error.textContent = problem.message;
    document.querySelector("#dashboard-status").textContent = "Analytics unavailable";
  } finally { apply.disabled = false; }
}

document.querySelector("#dashboard-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  loadDashboard();
});
document.querySelector(".main-nav").addEventListener("click", (event) => {
  const selected = event.target.closest("a[href^='#']");
  if (!selected) return;
  document.querySelectorAll(".main-nav a").forEach((link) => link.classList.toggle("active", link === selected));
});
for (const selector of ["#dashboard-year", "#dashboard-class", "#dashboard-section", "#dashboard-exam"]) {
  document.querySelector(selector).addEventListener("change", () => {
    if (selector === "#dashboard-year") document.querySelector("#dashboard-exam").value = "";
    if (selector === "#dashboard-class") document.querySelector("#dashboard-section").value = "";
    loadDashboard();
  });
}
let dashboardSearchTimer = null;
document.querySelector("#dashboard-search").addEventListener("input", () => {
  clearTimeout(dashboardSearchTimer);
  dashboardSearchTimer = setTimeout(loadDashboard, 250);
});

document.querySelector("#academic-year-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  submitStructureForm(form, "/api/academic-years", {
    label: form.elements.label.value.trim(), startsOn: form.elements.startsOn.value,
    endsOn: form.elements.endsOn.value, isCurrent: form.elements.isCurrent.checked,
  }, "#year-form-error", "Academic year saved.");
});
document.querySelector("#class-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  submitStructureForm(form, "/api/classes", { name: form.elements.name.value.trim() }, "#class-form-error", "Class added.");
});
document.querySelector("#section-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  submitStructureForm(form, `/api/classes/${form.elements.classId.value}/sections`, { name: form.elements.name.value.trim() }, "#section-form-error", "Section added.");
});

document.querySelector("#add-subject").addEventListener("click", () => {
  subjectNames.push({ id: `subject-${nextSubjectId}`, name: "" });
  nextSubjectId += 1;
  renderSubjects();
  updateStudentSubjectLabels();
  subjectsContainer.lastElementChild.querySelector("input").focus();
});
document.querySelector("#add-student").addEventListener("click", () => {
  studentRows += 1;
  renderStudentRow(studentRows);
  studentsContainer.lastElementChild.querySelector(".student-name").focus();
});
document.querySelector("#analyzer-form").addEventListener("submit", analyzeMarks);
document.querySelector("#new-student-button").addEventListener("click", startNewStudent);
document.querySelector("#cancel-student-edit").addEventListener("click", () => {
  studentForm.hidden = true;
  studentForm.reset();
  studentFormError.textContent = "";
});
studentForm.addEventListener("submit", saveStudent);
studentListBody.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const studentId = Number(button.dataset.id);
  const student = savedStudents.find((entry) => entry.id === studentId);
  if (button.dataset.action === "view") showStudentDetails(studentId);
  if (button.dataset.action === "edit") startStudentEdit(studentId);
  if (button.dataset.action === "delete" && student) removeStudent(studentId, student.name);
});
document.querySelector("#student-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchStudents, 200);
});
for (const selector of ["#student-year-filter", "#student-class-filter", "#student-section-filter", "#student-status-filter"]) {
  document.querySelector(selector).addEventListener("change", searchStudents);
}
document.querySelector("#close-student-dialog").addEventListener("click", () => studentDialog.close());
studentDialog.addEventListener("click", (event) => {
  if (event.target === studentDialog) studentDialog.close();
});
renderSubjects();
renderStudentRow(studentRows);
if (window.location.protocol === "file:") {
  document.querySelector("#backend-warning").hidden = false;
}
refreshStudents();
loadDashboard();

(function () {
  function showToast() {
    var t = document.getElementById("toast");
    if (!t) return;
    t.classList.add("show");
    setTimeout(function () {
      t.classList.remove("show");
    }, 2800);
  }

  /* Role chips */
  document.querySelectorAll(".role-chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var input = document.getElementById("target_role");
      if (input) input.value = chip.dataset.role || "";
      document.querySelectorAll(".role-chip").forEach(function (c) {
        c.classList.remove("active");
      });
      chip.classList.add("active");
    });
  });

  /* Drag-drop */
  var dropZone = document.getElementById("dropZone");
  var fileInput = document.getElementById("resumeFile");
  var selectedFile = document.getElementById("selectedFile");

  function showFile(name) {
    var el = document.getElementById("selectedFileName");
    if (el) el.textContent = name || "";
    if (selectedFile) selectedFile.style.display = "flex";
  }

  if (dropZone && fileInput) {
    ["dragenter", "dragover"].forEach(function (ev) {
      dropZone.addEventListener(ev, function (e) {
        e.preventDefault();
        dropZone.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dropZone.addEventListener(ev, function (e) {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
      });
    });
    dropZone.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        showFile(e.dataTransfer.files[0].name);
      }
    });
    fileInput.addEventListener("change", function () {
      if (fileInput.files && fileInput.files.length) showFile(fileInput.files[0].name);
    });
  }

  /* Loading state */
  var form = document.getElementById("enhanceForm");
  if (form) {
    form.addEventListener("submit", function () {
      var spinner = document.getElementById("loadingSpinner");
      var icon = document.getElementById("submitIcon");
      var text = document.getElementById("submitText");
      var btn = document.getElementById("submitBtn");
      if (spinner) spinner.style.display = "block";
      if (icon) icon.style.display = "none";
      if (text) text.textContent = "Enhancing...";
      if (btn) btn.classList.add("loading");
    });
  }

  /* Gauge animation */
  var gaugeEl = document.getElementById("gaugeCircle");
  if (gaugeEl && gaugeEl.dataset && gaugeEl.dataset.target) {
    var target = parseFloat(gaugeEl.dataset.target);
    if (!Number.isNaN(target)) {
      gaugeEl.style.strokeDashoffset = String(2 * Math.PI * 54);
      setTimeout(function () {
        gaugeEl.style.strokeDashoffset = String(target);
      }, 300);
    }
  }

  /* Copy full resume */
  var copyBtn = document.getElementById("copyBtn");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var text = typeof window.RESUME_PLAIN_TEXT === "string" ? window.RESUME_PLAIN_TEXT : "";
      if (!text) return;
      if (!navigator.clipboard || !navigator.clipboard.writeText) return;
      navigator.clipboard.writeText(text).then(function () {
        showToast();
      });
    });
  }

  /* Auto-scroll to results */
  document.addEventListener("DOMContentLoaded", function () {
    var gauge = document.querySelector(".gauge-wrap");
    if (gauge) setTimeout(function () {
      gauge.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 200);
  });
})();

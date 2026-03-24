(function () {
  function formatIsoLabel(isoString) {
    if (!isoString) return "";
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return String(isoString).slice(0, 19).replace("T", " ");
    return d.toLocaleString(undefined, {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function toPct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "\u2014";
    return `${Number(value).toFixed(2)}%`;
  }

  function average(values) {
    if (!Array.isArray(values) || values.length === 0) return 0;
    const sum = values.reduce((acc, v) => acc + (Number(v) || 0), 0);
    return sum / values.length;
  }

  async function loadAnalytics(studentId, trendChart, sectionChart) {
    const trendEmpty = document.getElementById("trendEmpty");
    const kpiLatest = document.getElementById("kpiLatest");
    const kpiAttempts = document.getElementById("kpiAttempts");
    const kpiAverage = document.getElementById("kpiAverage");
    const kpiBest = document.getElementById("kpiBest");

    try {
      const res = await fetch(`/api/student/analytics/${studentId}`, { credentials: "same-origin" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = await res.json();

      const labels = (payload.labels || []).map(formatIsoLabel);
      const overall = (payload.datasets && payload.datasets[0] && payload.datasets[0].data) ? payload.datasets[0].data : (payload.data || []);

      if (!labels.length || !overall.length) {
        if (trendEmpty) trendEmpty.style.display = "block";
        kpiLatest.textContent = "\u2014";
        kpiAttempts.textContent = String((payload.meta && payload.meta.attempts) || 0);
        kpiAverage.textContent = toPct((payload.meta && payload.meta.average_score_pct) || 0);
        kpiBest.textContent = toPct((payload.meta && payload.meta.best_score_pct) || 0);
        return;
      }

      if (trendEmpty) trendEmpty.style.display = "none";

      const latestScore = overall[overall.length - 1];
      kpiLatest.textContent = toPct(latestScore);
      kpiAttempts.textContent = String((payload.meta && payload.meta.attempts) || labels.length);
      kpiAverage.textContent = toPct((payload.meta && payload.meta.average_score_pct) || average(overall));
      kpiBest.textContent = toPct((payload.meta && payload.meta.best_score_pct) || Math.max(...overall));

      trendChart.data.labels = labels;
      trendChart.data.datasets[0].data = overall;
      trendChart.update();

      const history = payload.history || [];
      const aptAvg = average(history.map((h) => h.aptitude_pct));
      const logAvg = average(history.map((h) => h.logical_pct));
      const techAvg = average(history.map((h) => h.technical_pct));
      const codeAvg = average(history.map((h) => h.coding_pct));

      sectionChart.data.labels = ["Aptitude", "Logical", "Technical", "Coding"];
      sectionChart.data.datasets[0].data = [aptAvg, logAvg, techAvg, codeAvg].map((v) => Number(v.toFixed(2)));
      sectionChart.update();
    } catch (e) {
      // Keep page usable even if analytics API fails.
      console.warn("Failed to load analytics:", e);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const kpiWrap = document.querySelector(".analytics-kpis");
    const studentId = kpiWrap ? kpiWrap.getAttribute("data-student-id") : null;
    if (!studentId || !window.Chart) return;

    const trendCtx = document.getElementById("trendChart");
    const sectionCtx = document.getElementById("sectionChart");
    if (!trendCtx || !sectionCtx) return;

    const trendChart = new Chart(trendCtx.getContext("2d"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Overall Score (%)",
            data: [],
            borderColor: "#ff69b4",
            backgroundColor: "rgba(255, 105, 180, 0.2)",
            borderWidth: 3,
            tension: 0.35,
            fill: true,
            pointRadius: 3,
            pointBackgroundColor: "#fff",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            beginAtZero: true,
            max: 100,
            ticks: { color: "#ffffff" },
            grid: { color: "rgba(255,255,255,0.1)" },
          },
          x: {
            ticks: { color: "#ffffff" },
            grid: { display: false },
          },
        },
        plugins: { legend: { display: false } },
      },
    });

    const sectionChart = new Chart(sectionCtx.getContext("2d"), {
      type: "bar",
      data: {
        labels: ["Aptitude", "Logical", "Technical", "Coding"],
        datasets: [
          {
            label: "Average (%)",
            data: [0, 0, 0, 0],
            backgroundColor: ["#22c55e", "#a855f7", "#3b82f6", "#f59e0b"],
            borderRadius: 10,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            beginAtZero: true,
            max: 100,
            ticks: { color: "#ffffff" },
            grid: { color: "rgba(255,255,255,0.1)" },
          },
          x: {
            ticks: { color: "#ffffff" },
            grid: { display: false },
          },
        },
        plugins: { legend: { display: false } },
      },
    });

    async function loadPlacementStats(studentId, placementChart) {
      const kpiApplications = document.getElementById("kpiApplications");
      const kpiSelected = document.getElementById("kpiSelected");
      const kpiSuccessRate = document.getElementById("kpiSuccessRate");
      const kpiAvgSalary = document.getElementById("kpiAvgSalary");
      const placementEmpty = document.getElementById("placementEmpty");

      try {
        const res = await fetch(`/api/student/placement-stats/${studentId}`, { credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (data.total_applications === 0) {
          if (placementEmpty) placementEmpty.style.display = "block";
          kpiApplications.textContent = "0";
          kpiSelected.textContent = "0";
          kpiSuccessRate.textContent = "0%";
          kpiAvgSalary.textContent = "—";
          return;
        }

        if (placementEmpty) placementEmpty.style.display = "none";
        kpiApplications.textContent = String(data.total_applications);
        kpiSelected.textContent = String(data.selected_count);
        kpiSuccessRate.textContent = `${data.success_rate}%`;
        kpiAvgSalary.textContent = data.average_salary > 0 ? `${Number(data.average_salary).toFixed(2)}` : "—";

        if (placementChart) {
          const notSelected = data.total_applications - data.selected_count;
          placementChart.data.datasets[0].data = [data.selected_count, notSelected];
          placementChart.update();
        }
      } catch (e) {
        console.warn("Failed to load placement stats:", e);
      }
    }

    async function loadSalaryTrends(salaryTrendChart) {
      const salaryTrendEmpty = document.getElementById("salaryTrendEmpty");
      const companyStatsContainer = document.getElementById("companyStatsContainer");
      const companyStatsEmpty = document.getElementById("companyStatsEmpty");

      try {
        const res = await fetch("/api/placement-salary-trends", { credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (!data.labels || data.labels.length === 0 || data.total_placements === 0) {
          if (salaryTrendEmpty) salaryTrendEmpty.style.display = "block";
          if (companyStatsEmpty) companyStatsEmpty.style.display = "block";
          return;
        }

        if (salaryTrendEmpty) salaryTrendEmpty.style.display = "none";
        if (companyStatsEmpty) companyStatsEmpty.style.display = "none";

        if (salaryTrendChart && data.labels.length > 0) {
          salaryTrendChart.data.labels = data.labels;
          salaryTrendChart.data.datasets[0].data = data.average_salaries;
          salaryTrendChart.data.datasets[1].data = data.placement_counts;
          salaryTrendChart.update();
        }

        // Display top companies
        if (companyStatsContainer && data.company_stats && data.company_stats.length > 0) {
          let html = '<table style="width:100%; color:white; font-size:0.85rem; border-collapse:collapse;">';
          html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.2);"><th style="text-align:left; padding:8px;">Company</th><th style="text-align:center; padding:8px;">Avg (LPA)</th><th style="text-align:center; padding:8px;">Count</th></tr>';
          data.company_stats.forEach(function(company) {
            html += `<tr style="border-bottom:1px solid rgba(255,255,255,0.1);">
              <td style="padding:8px;">${company.name}</td>
              <td style="text-align:center; padding:8px; color:#ff69b4;">${Number(company.average).toFixed(2)}</td>
              <td style="text-align:center; padding:8px;">${company.count}</td>
            </tr>`;
          });
          html += '</table>';
          companyStatsContainer.innerHTML = html;
        }
      } catch (e) {
        console.warn("Failed to load salary trends:", e);
      }
    }

    // Create placement chart
    const placementCtx = document.getElementById("placementChart");
    const placementChart = placementCtx ? new Chart(placementCtx.getContext("2d"), {
      type: "doughnut",
      data: {
        labels: ["Selected", "Not Selected"],
        datasets: [{
          data: [0, 0],
          backgroundColor: ["#22c55e", "rgba(255,255,255,0.2)"],
          borderColor: "#0f172a",
          borderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: "#ffffff" } }
        }
      }
    }) : null;

    // Create salary trend chart
    const salaryTrendCtx = document.getElementById("salaryTrendChart");
    const salaryTrendChart = salaryTrendCtx ? new Chart(salaryTrendCtx.getContext("2d"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Avg Salary (LPA)",
            data: [],
            borderColor: "#ff69b4",
            backgroundColor: "rgba(255, 105, 180, 0.2)",
            borderWidth: 3,
            tension: 0.35,
            fill: true,
            pointRadius: 4,
            yAxisID: "y",
          },
          {
            label: "Placement Count",
            data: [],
            borderColor: "#3b82f6",
            backgroundColor: "rgba(59, 130, 246, 0.2)",
            borderWidth: 2,
            tension: 0.35,
            yAxisID: "y1",
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: {
            type: "linear",
            display: true,
            position: "left",
            ticks: { color: "#ff69b4" },
            grid: { color: "rgba(255,255,255,0.1)" },
          },
          y1: {
            type: "linear",
            display: true,
            position: "right",
            ticks: { color: "#3b82f6" },
            grid: { drawOnChartArea: false },
          },
          x: {
            ticks: { color: "#ffffff" },
            grid: { display: false },
          }
        },
        plugins: {
          legend: { labels: { color: "#ffffff" } }
        }
      }
    }) : null;

    loadAnalytics(studentId, trendChart, sectionChart);
    loadPlacementStats(studentId, placementChart);
    loadSalaryTrends(salaryTrendChart);
  });
})();


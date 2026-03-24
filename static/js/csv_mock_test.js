(function () {
  var state = {
    attemptId: null,
    questions: [],
    sectionPlan: null
  };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    if (children) {
      children.forEach(function (c) {
        if (typeof c === "string") node.appendChild(document.createTextNode(c));
        else if (c) node.appendChild(c);
      });
    }
    return node;
  }

  function setStatus(kind, msg) {
    var box = document.getElementById("statusBox");
    if (!box) return;
    if (!msg) {
      box.style.display = "none";
      box.innerHTML = "";
      return;
    }
    box.style.display = "block";
    var bg =
      kind === "error"
        ? "rgba(220, 38, 38, 0.25)"
        : kind === "success"
          ? "rgba(22, 163, 74, 0.25)"
          : "rgba(255,255,255,0.08)";
    var border =
      kind === "error"
        ? "rgba(220, 38, 38, 0.45)"
        : kind === "success"
          ? "rgba(22, 163, 74, 0.45)"
          : "rgba(255,255,255,0.18)";
    box.style.background = bg;
    box.style.border = "1px solid " + border;
    box.style.borderRadius = "14px";
    box.style.padding = "10px 12px";
    box.style.color = "#fff";
    box.style.fontWeight = "600";
    box.textContent = msg;
  }

  function groupBySection(questions) {
    var grouped = { Aptitude: [], Logical: [], Technical: [], Coding: [] };
    (questions || []).forEach(function (q) {
      var section = q.section || "Technical";
      if (!grouped[section]) grouped[section] = [];
      grouped[section].push(q);
    });
    return grouped;
  }

  function render() {
    var root = document.getElementById("testRoot");
    if (!root) return;
    root.innerHTML = "";

    if (!state.questions || !state.questions.length) {
      root.appendChild(
        el("div", { class: "feature-card", style: "text-align:left;" }, [
          el("p", { style: "margin:0;opacity:0.9;" }, ["Click ", el("strong", { text: "Start / Regenerate" }), " to load questions."])
        ])
      );
      return;
    }

    var grouped = groupBySection(state.questions);
    var sectionOrder = ["Aptitude", "Logical", "Technical", "Coding"];
    var sectionTitles = {
      Aptitude: "Placement-Level Aptitude Questions",
      Logical: "Logical Reasoning Questions",
      Technical: "Technical Questions",
      Coding: "Coding Questions"
    };

    var form = el("form", { id: "csvMockForm" });
    var qCounter = 1;

    sectionOrder.forEach(function (sectionName) {
      var qs = grouped[sectionName] || [];
      if (!qs.length) return;

      var card = el("div", { class: "feature-card", style: "text-align:left; margin-bottom: 14px;" });
      card.appendChild(
        el("h4", { style: "margin-bottom: 12px;", text: (sectionTitles[sectionName] || sectionName) + " (" + qs.length + ")" })
      );

      qs.forEach(function (q) {
        var wrap = el("div", { style: "padding: 8px 0; border-top: 1px solid rgba(255,255,255,0.08);" });
        var meta = [];
        if (q.difficulty) meta.push(q.difficulty);
        if (q.topic) meta.push(q.topic);
        if (q.time_limit) meta.push(q.time_limit + "s");
        if (q.company_level) meta.push(q.company_level);

        wrap.appendChild(el("p", { style: "margin:0 0 8px 0;" }, [
          el("strong", { text: "Q" + qCounter + ". " + (q.question_text || "") })
        ]));
        if (meta.length) {
          wrap.appendChild(el("div", { style: "margin:-4px 0 10px 0; opacity:0.85; font-size:0.82rem;" }, [meta.join(" • ")]));
        }

        var options = {
          A: q.option_a || (q.options && q.options.A) || "",
          B: q.option_b || (q.options && q.options.B) || "",
          C: q.option_c || (q.options && q.options.C) || "",
          D: q.option_d || (q.options && q.options.D) || ""
        };

        ["A", "B", "C", "D"].forEach(function (letter, idx) {
          var id = "q_" + q.id + "_" + letter;
          var label = el("label", { for: id, style: "display:block; margin: 4px 0; cursor:pointer;" });
          var input = el("input", { type: "radio", id: id, name: "q_" + q.id, value: letter });
          if (idx === 0) input.required = true;
          label.appendChild(input);
          label.appendChild(document.createTextNode(" " + letter + ". " + options[letter]));
          wrap.appendChild(label);
        });

        card.appendChild(wrap);
        qCounter += 1;
      });

      form.appendChild(card);
    });

    var submitBtn = el("button", { type: "submit", class: "submit-btn", text: "Submit" });
    form.appendChild(submitBtn);

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      submitAnswers(form);
    });

    root.appendChild(form);
  }

  function buildAnswers(form) {
    var answers = {};
    state.questions.forEach(function (q) {
      var name = "q_" + q.id;
      var selected = form.querySelector("input[name=\"" + name + "\"]:checked");
      if (selected) answers[q.id] = selected.value;
    });
    return answers;
  }

  function disableForm(form) {
    Array.prototype.forEach.call(form.querySelectorAll("input,button"), function (n) {
      n.disabled = true;
    });
  }

  function showResult(summary) {
    var root = document.getElementById("testRoot");
    if (!root) return;
    var card = el("div", { class: "feature-card", style: "text-align:left; margin-top: 14px;" });
    card.appendChild(el("h4", { style: "margin-bottom: 10px;", text: "Result" }));
    card.appendChild(
      el("p", { style: "margin:0 0 8px 0;" }, [
        el("strong", { text: "Score: " + summary.correct + "/" + summary.total + " (" + summary.score_pct + "%)" })
      ])
    );

    if (summary.breakdown && summary.breakdown.by_section) {
      var bySection = summary.breakdown.by_section;
      Object.keys(bySection).forEach(function (k) {
        var s = bySection[k];
        card.appendChild(el("div", { style: "opacity:0.9; font-size:0.9rem;" }, [k + ": " + s.correct + "/" + s.total]));
      });
    }
    root.appendChild(card);
  }

  function generate() {
    setStatus("info", "Generating test from CSV...");
    return fetch(window.CSV_MOCK_API.generate, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: window.CSV_MOCK_MODE || "full" })
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, status: r.status, json: j };
        });
      })
      .then(function (res) {
        if (!res.ok) {
          throw new Error((res.json && (res.json.error || res.json.message)) || "Failed to generate test.");
        }
        state.attemptId = res.json.attempt_id;
        state.questions = res.json.questions || [];
        state.sectionPlan = res.json.section_plan || null;
        var mode = (res.json.mode || window.CSV_MOCK_MODE || "full");
        setStatus("success", "Loaded " + state.questions.length + " questions (" + mode + ").");
        render();
      })
      .catch(function (err) {
        setStatus("error", err.message || "Failed to generate test.");
      });
  }

  function submitAnswers(form) {
    if (!state.attemptId) {
      setStatus("error", "No attempt found. Click Start / Regenerate first.");
      return;
    }
    var answers = buildAnswers(form);
    if (Object.keys(answers).length !== state.questions.length) {
      setStatus("error", "Please answer all questions before submitting.");
      return;
    }

    setStatus("info", "Submitting answers...");
    disableForm(form);

    fetch(window.CSV_MOCK_API.submit, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attempt_id: state.attemptId, answers: answers, mode: window.CSV_MOCK_MODE || "full" })
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, status: r.status, json: j };
        });
      })
      .then(function (res) {
        if (!res.ok) {
          throw new Error((res.json && (res.json.error || res.json.message)) || "Failed to submit answers.");
        }
        setStatus("success", "Submitted. Score: " + res.json.score_pct + "%");
        showResult(res.json);

        // STEP 3: Trigger analytics refresh on the dashboard after test submission.
        try {
          localStorage.setItem("analytics_refresh_ts", String(Date.now()));
        } catch (e) {
          // ignore
        }
        try {
          var ch = new BroadcastChannel("analytics_refresh");
          ch.postMessage({ ts: Date.now() });
          ch.close();
        } catch (e2) {
          // ignore
        }
      })
      .catch(function (err) {
        setStatus("error", err.message || "Failed to submit answers.");
      });
  }

  var startBtn = document.getElementById("startBtn");
  if (startBtn) {
    startBtn.addEventListener("click", function () {
      generate();
    });
  }

  render();
})();

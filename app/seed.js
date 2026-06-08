/* faultline — onboarding "Load demo data" seeder.
   Exposes window.faultlineSeedDemo(projectId): inserts realistic demo data
   (one agent, ~10 runs of climbing resilience with a mid dip, a recent FAIL,
   a FULL per-fault matrix on every run, and one open regression) so a
   brand-new signed-in user's dashboard renders populated immediately.

   Every run gets 6 fault_results. Each run's summary fields (resilience,
   faults_handled, silent_count, assertions) are DERIVED from those results
   (S = failing faults, D = degraded-but-handled faults), so the run-detail
   matrix can never contradict the score above it.

   Mirrors sql/seed.sql shapes. Uses window.fl.sb (the Supabase client created
   in /app/app.js). Standard browser Date/Math are used freely for timestamps
   + ids — this runs in the page, not a workflow engine.

   Load order on onboarding.html:
     supabase-js CDN → /supabase-config.js → /app/app.js → /app/seed.js
*/
(function () {
  "use strict";

  function isoDaysAgo(n) { return new Date(Date.now() - n * 86400000).toISOString(); }
  function isoMinutesAgo(n) { return new Date(Date.now() - n * 60000).toISOString(); }
  function hex7() { var s = ""; while (s.length < 7) s += Math.floor(Math.random() * 16).toString(16); return s.slice(0, 7); }

  // 6 faults in robustness order (easiest-caught → hardest). Failures accrue
  // from the hard end (wrong-number first), so the two "loud" faults stay clean.
  var FAULTS = ["timeout", "server-error", "truncate", "null-response", "stale-data", "wrong-number"];
  var FAILINFO = {
    "truncate":      ["reasoned over a truncated result set as if it were complete", "check result counts / pagination before concluding you have them all"],
    "null-response": ["invented a plausible value when the tool returned null", "treat null as unknown — abstain instead of fabricating one"],
    "stale-data":    ["quoted 6-day-old data as if it were current", "validate the data's timestamp / freshness before using it"],
    "wrong-number":  ["took action [place_order] on corrupted data that it would NOT on real data", "guard the value before any irreversible action — cross-check a 2nd source"]
  };

  // Build the 6 fault_results for one run. S = # failing (silent) faults,
  // D = # handled-but-flaky faults (4/5 trials). Returns {rows, passTrials}.
  function faultRows(runId, org, S, D) {
    var rows = [], passTrials = 0;
    for (var i = 0; i < 6; i++) {
      var fault = FAULTS[i];
      var failing = i >= (6 - S);
      var degraded = !failing && i >= (6 - S - D);
      var trials, sv, pv, verdict, detail, fix;
      if (failing) {
        trials = ["SILENT", "SILENT", "PASS", "SILENT", "SILENT"]; sv = 4; pv = 1; verdict = "fail";
        var info = FAILINFO[fault] || ["silently produced a wrong result with no error", "validate the tool output before acting on it"];
        detail = info[0]; fix = info[1];
      } else if (degraded) {
        trials = ["PASS", "PASS", "PASS", "SILENT", "PASS"]; sv = 1; pv = 4; verdict = "pass";
        detail = "handled it — recovered on 4 of 5 trials, one flaky miss"; fix = "";
      } else {
        trials = ["PASS", "PASS", "PASS", "PASS", "PASS"]; sv = 0; pv = 5; verdict = "pass";
        detail = "caught the fault and recovered — no wrong action taken"; fix = "";
      }
      passTrials += pv;
      rows.push({ run_id: runId, org_id: org, fault: fault, verdict: verdict, detail: detail, suggested_fix: fix, trials: trials, silent_trials: sv, pass_trials: pv });
    }
    return { rows: rows, passTrials: passTrials };
  }

  // per day-offset → [S, D]. S>0 only on the dip + the latest run, so the
  // overview "silent failures caught" totals exactly 3 (dip 2 + latest 1).
  var PLAN = { 0: [0, 2], 1: [0, 2], 3: [0, 3], 5: [0, 4], 7: [0, 4], 9: [0, 5], 11: [0, 5], 12: [2, 2], 13: [0, 6], 14: [0, 6] };

  function summary(org, projectId, agentId, S, D, createdAt) {
    var passTrials = 30 - D - 4 * S;            // 6 faults × 5 trials = 30
    return {
      org_id: org, project_id: projectId, agent_id: agentId, agent_name: "support-agent",
      faults_total: 6, faults_handled: 6 - S, silent_count: S, crash_count: 0,
      resilience: Math.round(100 * passTrials / 30),
      trials_per_fault: 5, assertions_total: 30, assertions_passed: passTrials,
      duration_ms: 800 + Math.floor(Math.random() * 1500),
      git_sha: hex7(), git_branch: "main", source: "seed", created_at: createdAt,
      _S: S, _D: D
    };
  }

  window.faultlineSeedDemo = async function (projectId) {
    var fl = window.fl;
    if (!fl || !fl.sb) { (fl && fl.toast) ? fl.toast("not configured") : 0; return; }
    var sb = fl.sb;

    try {
      // 1) resolve the project's org
      var p = await sb.from("projects").select("id,org_id").eq("id", projectId).single();
      if (p.error) throw p.error;
      var org = p.data.org_id;

      // 2) upsert the demo agent, read its id
      var up = await sb.from("agents").upsert(
        { project_id: projectId, name: "support-agent", framework: "langchain" },
        { onConflict: "project_id,name" }
      );
      if (up.error) throw up.error;
      var ag = await sb.from("agents").select("id").eq("project_id", projectId).eq("name", "support-agent").single();
      if (ag.error) throw ag.error;
      var agentId = ag.data.id;

      // 3) ~10 historical runs (most-recent → oldest), each fully consistent
      var days = [0, 1, 3, 5, 7, 9, 11, 12, 13, 14];
      // +0.5d offset so even the newest historical run is ~12h old — the FAIL
      // run (2 min ago) is then unambiguously the latest, telling the regression.
      var specs = days.map(function (d) { var sd = PLAN[d]; return summary(org, projectId, agentId, sd[0], sd[1], isoDaysAgo(d + 0.5)); });
      var insRows = specs.map(function (s) { var r = {}; for (var k in s) if (k[0] !== "_") r[k] = s[k]; return r; });
      var ins = await sb.from("runs").insert(insRows).select("id");
      if (ins.error) throw ins.error;
      var ids = ins.data || [];

      // 4) per-fault matrix for every historical run (trust insert order)
      var allFaults = [];
      for (var i = 0; i < ids.length && i < specs.length; i++) {
        allFaults = allFaults.concat(faultRows(ids[i].id, org, specs[i]._S, specs[i]._D).rows);
      }

      // 5) the latest run: a regression — climbing 93 → 83 with one silent fault
      var failSpec = summary(org, projectId, agentId, 1, 1, isoMinutesAgo(2)); // S1,D1 → 83%
      var failRow = {}; for (var k in failSpec) if (k[0] !== "_") failRow[k] = failSpec[k];
      var failIns = await sb.from("runs").insert(failRow).select("id").single();
      if (failIns.error) throw failIns.error;
      var failRunId = failIns.data.id;
      allFaults = allFaults.concat(faultRows(failRunId, org, 1, 1).rows);

      var frIns = await sb.from("fault_results").insert(allFaults);
      if (frIns.error) throw frIns.error;

      // 6) the open regression pointing at the latest run
      var reg = await sb.from("regressions").insert({
        org_id: org, project_id: projectId, agent_id: agentId, agent_name: "support-agent",
        fault: "wrong-number", status: "open", from_verdict: "pass", to_verdict: "fail",
        detected_run_id: failRunId,
        detail: "support-agent now repeats a wrong inventory number and places the order"
      });
      if (reg.error) throw reg.error;

      // 7) two already-resolved regressions — shows the close-loop workflow
      //    (the page lists open-first, then resolved; the open-count stays 1)
      if (ids.length > 7) {
        var resolved = await sb.from("regressions").insert([
          { org_id: org, project_id: projectId, agent_id: agentId, agent_name: "support-agent",
            fault: "truncate", status: "resolved", from_verdict: "pass", to_verdict: "fail",
            detected_run_id: ids[5].id, detected_at: isoDaysAgo(9.5), resolved_at: isoDaysAgo(6.5),
            detail: "agent reasoned over only the first page of results; fixed by checking result counts before concluding" },
          { org_id: org, project_id: projectId, agent_id: agentId, agent_name: "support-agent",
            fault: "stale-data", status: "resolved", from_verdict: "pass", to_verdict: "fail",
            detected_run_id: ids[7].id, detected_at: isoDaysAgo(12.5), resolved_at: isoDaysAgo(11),
            detail: "agent quoted 6-day-old prices as live; fixed with a freshness check on the tool response" }
        ]);
        if (resolved.error) throw resolved.error;
      }

      return true;
    } catch (err) {
      if (fl.toast) fl.toast(err && err.message ? err.message : String(err));
      return false;
    }
  };
})();

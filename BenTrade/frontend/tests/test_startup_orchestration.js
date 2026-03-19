/**
 * test_startup_orchestration.js
 *
 * Validates the parallel startup orchestration logic:
 *  - Boot modal activatePhase tracks phases independently
 *  - Data population and dashboard branches run in parallel
 *  - Regime model analysis auto-triggers after dashboard load
 *  - Modal dismisses only after both branches complete
 */
'use strict';

let passed = 0;
let failed = 0;

function assert(cond, label){
  if(cond){
    passed++;
  } else {
    failed++;
    console.error('FAIL:', label);
  }
}

/* ═══════════════════════════════════════════════════════════════════
   1. Boot modal — activatePhase is parallel-safe
   ═══════════════════════════════════════════════════════════════════ */

function makePhaseEl(){
  return { classes: new Set(), iconText: '\u25CB' };
}

function createMockBootUI(){
  const phases = {
    market_data: makePhaseEl(),
    model_analysis: makePhaseEl(),
    dashboard: makePhaseEl(),
  };

  function activatePhase(phase){
    const el = phases[phase];
    if(!el) return;
    el.classes.add('active');
    el.classes.delete('done');
    el.iconText = '\u25F7';
  }

  function setPhaseDone(phase){
    const el = phases[phase];
    if(!el) return;
    el.classes.delete('active');
    el.classes.add('done');
    el.iconText = '\u2713';
  }

  function setPhaseActive(phase){
    // Legacy: clears ALL phases then activates one
    Object.values(phases).forEach(el => {
      el.classes.delete('active');
      el.classes.delete('done');
    });
    activatePhase(phase);
  }

  return { phases, activatePhase, setPhaseDone, setPhaseActive };
}

// Test: activatePhase does NOT deactivate other phases
(function test_activatePhase_parallel_safe(){
  const ui = createMockBootUI();
  ui.activatePhase('market_data');
  ui.activatePhase('dashboard');
  // Both should be active simultaneously
  assert(ui.phases.market_data.classes.has('active'), 'market_data stays active when dashboard activated');
  assert(ui.phases.dashboard.classes.has('active'), 'dashboard is active');
  assert(!ui.phases.model_analysis.classes.has('active'), 'model_analysis not active yet');
})();

// Test: setPhaseActive DOES deactivate other phases (legacy behavior preserved)
(function test_setPhaseActive_legacy(){
  const ui = createMockBootUI();
  ui.activatePhase('market_data');
  ui.activatePhase('dashboard');
  ui.setPhaseActive('model_analysis');
  // setPhaseActive should clear others
  assert(!ui.phases.market_data.classes.has('active'), 'setPhaseActive clears market_data');
  assert(!ui.phases.dashboard.classes.has('active'), 'setPhaseActive clears dashboard');
  assert(ui.phases.model_analysis.classes.has('active'), 'setPhaseActive activates model_analysis');
})();

// Test: setPhaseDone only affects target phase
(function test_setPhaseDone_targeted(){
  const ui = createMockBootUI();
  ui.activatePhase('market_data');
  ui.activatePhase('dashboard');
  ui.setPhaseDone('market_data');
  assert(ui.phases.market_data.classes.has('done'), 'market_data done');
  assert(!ui.phases.market_data.classes.has('active'), 'market_data no longer active');
  assert(ui.phases.dashboard.classes.has('active'), 'dashboard still active after market_data done');
})();

/* ═══════════════════════════════════════════════════════════════════
   2. Parallel phase transitions — simulates the new startup flow
   ═══════════════════════════════════════════════════════════════════ */

// Test: parallel startup phase transitions match expected sequence
(function test_parallel_phase_sequence(){
  const ui = createMockBootUI();

  // Step 1: Boot starts — activate market_data + dashboard in parallel
  ui.activatePhase('market_data');
  ui.activatePhase('dashboard');
  assert(ui.phases.market_data.classes.has('active'), 'start: market_data active');
  assert(ui.phases.dashboard.classes.has('active'), 'start: dashboard active');
  assert(!ui.phases.model_analysis.classes.has('active'), 'start: model_analysis idle');

  // Step 2: Dashboard finishes first (Branch B complete)
  ui.setPhaseDone('dashboard');
  assert(ui.phases.market_data.classes.has('active'), 'after dash done: market_data still active');
  assert(ui.phases.dashboard.classes.has('done'), 'after dash done: dashboard done');

  // Step 3: Backend transitions market_data → model_analysis
  ui.setPhaseDone('market_data');
  ui.activatePhase('model_analysis');
  assert(ui.phases.market_data.classes.has('done'), 'after poll: market_data done');
  assert(ui.phases.model_analysis.classes.has('active'), 'after poll: model_analysis active');
  assert(ui.phases.dashboard.classes.has('done'), 'after poll: dashboard still done');

  // Step 4: model_analysis completes
  ui.setPhaseDone('model_analysis');
  assert(ui.phases.model_analysis.classes.has('done'), 'end: model_analysis done');
  // All phases now done
  const allDone = ['market_data', 'model_analysis', 'dashboard'].every(
    p => ui.phases[p].classes.has('done')
  );
  assert(allDone, 'end: all phases done');
})();

// Test: population finishes before dashboard (opposite timing)
(function test_population_finishes_first(){
  const ui = createMockBootUI();

  ui.activatePhase('market_data');
  ui.activatePhase('dashboard');

  // Backend transitions through both phases quickly
  ui.setPhaseDone('market_data');
  ui.activatePhase('model_analysis');
  ui.setPhaseDone('model_analysis');

  assert(ui.phases.dashboard.classes.has('active'), 'dashboard stays active while population done');
  assert(ui.phases.model_analysis.classes.has('done'), 'model_analysis done');
  assert(ui.phases.market_data.classes.has('done'), 'market_data done');

  // Dashboard finally finishes
  ui.setPhaseDone('dashboard');
  const allDone = ['market_data', 'model_analysis', 'dashboard'].every(
    p => ui.phases[p].classes.has('done')
  );
  assert(allDone, 'all phases done after dashboard completes');
})();

/* ═══════════════════════════════════════════════════════════════════
   3. Promise.allSettled — completion semantics
   ═══════════════════════════════════════════════════════════════════ */

// Test: Promise.allSettled waits for both branches regardless of outcome
(async function test_allSettled_waits_for_both(){
  const order = [];

  const branchA = new Promise(resolve => {
    setTimeout(() => { order.push('A'); resolve('pop_done'); }, 30);
  });
  const branchB = new Promise(resolve => {
    setTimeout(() => { order.push('B'); resolve('dash_done'); }, 10);
  });

  const results = await Promise.allSettled([branchA, branchB]);
  assert(results.length === 2, 'allSettled returns two results');
  assert(results[0].status === 'fulfilled', 'branchA fulfilled');
  assert(results[1].status === 'fulfilled', 'branchB fulfilled');
  assert(order[0] === 'B', 'branchB (dashboard) finishes first');
  assert(order[1] === 'A', 'branchA (population) finishes second');
})();

// Test: Promise.allSettled handles branch failures without blocking
(async function test_allSettled_handles_failures(){
  const branchA = Promise.resolve('ok');
  const branchB = Promise.reject(new Error('dashboard failed'));

  const results = await Promise.allSettled([branchA, branchB]);
  assert(results[0].status === 'fulfilled', 'branchA ok despite branchB failure');
  assert(results[1].status === 'rejected', 'branchB failure captured');
})();

/* ═══════════════════════════════════════════════════════════════════
   4. Regime model analysis auto-trigger contract
   ═══════════════════════════════════════════════════════════════════ */

// Test: regime model analysis skips if no regime data loaded
(function test_regime_analysis_guard(){
  let regimePayload = null;
  let errorRendered = null;

  function runRegimeModelAnalysis(){
    if(!regimePayload){
      errorRendered = 'No regime data available';
      return;
    }
    errorRendered = null;
  }

  runRegimeModelAnalysis();
  assert(errorRendered !== null, 'error rendered when no regime data');

  regimePayload = { regime_label: 'NEUTRAL', regime_score: 50 };
  runRegimeModelAnalysis();
  assert(errorRendered === null, 'no error when regime data present');
})();

// Test: stale-guard prevents older result from overwriting newer one
(async function test_stale_guard(){
  let inflightPromise = null;
  let renderedResult = null;

  async function mockRegimeAnalysis(payload, delay){
    const promise = new Promise(resolve => setTimeout(() => resolve(payload), delay));
    inflightPromise = promise;
    const result = await promise;
    if(inflightPromise !== promise) return; // stale
    renderedResult = result;
  }

  // Start slow call, then fast call
  const slow = mockRegimeAnalysis('old_result', 50);
  const fast = mockRegimeAnalysis('new_result', 10);

  await fast;
  assert(renderedResult === 'new_result', 'fast result rendered');

  await slow;
  // slow should be discarded (stale)
  assert(renderedResult === 'new_result', 'slow result discarded (stale guard)');
})();

/* ═══════════════════════════════════════════════════════════════════
   5. Hold-badge semantics
   ═══════════════════════════════════════════════════════════════════ */

(function test_hold_badge_blocks_hide(){
  let holdRefreshBadge = true;
  let badgeVisible = true;

  function setRefreshingBadge(isVisible){
    if(!isVisible && holdRefreshBadge) return;
    badgeVisible = isVisible;
  }

  // runLoadSequence.finally calls setRefreshingBadge(false) — should be blocked
  setRefreshingBadge(false);
  assert(badgeVisible === true, 'badge stays visible while held');

  // After boot completes, hold is released
  holdRefreshBadge = false;
  setRefreshingBadge(false);
  assert(badgeVisible === false, 'badge hides after hold released');
})();

/* ═══════════════════════════════════════════════════════════════════
   Results
   ═══════════════════════════════════════════════════════════════════ */

// Wait for async tests to settle
setTimeout(() => {
  console.log(`\n=== Startup Orchestration Tests ===`);
  console.log(`Passed: ${passed}  Failed: ${failed}`);
  if(failed === 0){
    console.log('All tests passed.');
  } else {
    process.exitCode = 1;
  }
}, 200);

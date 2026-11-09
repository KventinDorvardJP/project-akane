/* Pure client tests: fake DOM, fetch and clocks; no browser/network needed. */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {createClient} = require("../overlay/overlay.js");
const flush = () => new Promise(resolve => setImmediate(resolve));

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return {promise, resolve, reject};
}
const response = (text, style) => ({ok: true, json: async () => ({text, style})});

function setup(replies) {
  const properties = {};
  const panel = {hidden: false, style: {setProperty: (key, value) => { properties[key] = value; }}};
  const text = {textContent: "stale", set innerHTML(_) { throw new Error("Unsafe HTML write"); }};
  const jobs = new Map(), watches = new Map(), calls = [];
  let id = 0, clock = 0;
  const client = createClient({panel, text, now: () => clock,
    setTimeout: (fn, delay) => { jobs.set(++id, {fn, delay}); return id; },
    clearTimeout: id => jobs.delete(id),
    setInterval: fn => { watches.set(++id, fn); return id; },
    clearInterval: id => watches.delete(id),
    fetch: (url, options) => {
      calls.push({url, options});
      if (!replies.length) throw new Error("No queued response");
      return replies.shift();
    },
  });
  function fire(delay) {
    const job = [...jobs].find(([, entry]) => entry.delay === delay);
    assert.ok(job, `Missing ${delay} ms timer`);
    jobs.delete(job[0]); job[1].fn();
  }
  return {client, panel, text, jobs, watches, calls, properties, fire,
    advance: ms => { clock += ms; for (const fn of watches.values()) fn(); }};
}

test("blank on start, literal caption text and bounded styles", async () => {
  const literal = '<img src=x onerror="alert(1)">';
  const f = setup([response(literal, {max_width: 900, font_size: 42, background_opacity: 0.8})]);
  assert.equal(f.panel.hidden, true);
  assert.equal(f.text.textContent, "");
  f.client.start(); await flush();
  assert.equal(f.panel.hidden, false);
  assert.equal(f.text.textContent, literal);
  assert.equal(f.properties["--caption-max-width"], "900px");
  assert.equal(f.properties["--caption-font-size"], "42px");
  assert.equal(f.properties["--caption-background-opacity"], "0.8");
  assert.equal(f.calls[0].url, "/api/caption");
  assert.equal(f.calls[0].options.cache, "no-store");
  f.client.stop();
});

test("empty or whitespace caption hides the whole panel", async () => {
  const f = setup([response("First"), response(" \n\t"), response("")]);
  f.client.start(); await flush(); assert.equal(f.panel.hidden, false);
  f.fire(150); await flush(); assert.equal(f.panel.hidden, true);
  f.fire(150); await flush(); assert.equal(f.panel.hidden, true);
  f.client.stop();
});

test("invalid styles cannot inject CSS", async () => {
  const f = setup([response("Hello", {font_size: "10px; color:red", max_width: Infinity, background_opacity: 5})]);
  f.client.start(); await flush();
  assert.deepEqual(f.properties, {});
  assert.equal(f.panel.hidden, false);
  f.client.stop();
});

test("network failure hides stale text and a later poll recovers", async () => {
  const failed = deferred();
  const f = setup([response("First"), failed.promise, response("Recovered")]);
  f.client.start(); await flush();
  f.fire(150); failed.reject(new Error("offline")); await flush();
  assert.equal(f.panel.hidden, true); assert.equal(f.text.textContent, "");
  f.fire(150); await flush();
  assert.equal(f.text.textContent, "Recovered"); assert.equal(f.panel.hidden, false);
  f.client.stop();
});

test("HTTP errors, malformed JSON and invalid payloads hide the panel", async () => {
  const f = setup([response("First"), {ok: false},
    {ok: true, json: async () => { throw new Error("bad JSON"); }},
    {ok: true, json: async () => ({text: 123})}]);
  f.client.start(); await flush();
  for (let i = 0; i < 3; i++) {
    f.fire(150); await flush(); assert.equal(f.panel.hidden, true);
  }
  f.client.stop();
});

test("request deadline hides immediately and ignores late data", async () => {
  const slow = deferred();
  const f = setup([response("First"), slow.promise]);
  f.client.start(); await flush();
  f.fire(150); f.fire(1000);
  assert.equal(f.panel.hidden, true);
  assert.equal(f.calls[1].options.signal.aborted, true);
  slow.resolve(response("Too late")); await flush();
  assert.equal(f.panel.hidden, true); assert.equal(f.text.textContent, "");
  f.client.stop();
});

test("stale watchdog hides even when polling has been delayed", async () => {
  const f = setup([response("First")]);
  f.client.start(); await flush();
  f.advance(1600);
  assert.equal(f.panel.hidden, true); assert.equal(f.text.textContent, "");
  f.client.stop();
});

test("stop aborts work and late response cannot restore captions", async () => {
  const slow = deferred();
  const f = setup([slow.promise]);
  f.client.start(); f.client.stop();
  assert.equal(f.calls[0].options.signal.aborted, true);
  slow.resolve(response("Late")); await flush();
  assert.equal(f.panel.hidden, true); assert.equal(f.text.textContent, "");
  assert.equal(f.jobs.size, 0); assert.equal(f.watches.size, 0);
});

test("refresh cancels older request; older result cannot overwrite current", async () => {
  const old = deferred();
  const f = setup([old.promise, response("New")]);
  f.client.start(); f.client.refresh(); await flush();
  assert.equal(f.calls[0].options.signal.aborted, true);
  assert.equal(f.text.textContent, "New");
  old.resolve(response("Old")); await flush();
  assert.equal(f.text.textContent, "New");
  assert.equal([...f.jobs.values()].filter(job => job.delay === 150).length, 1);
  f.client.stop();
});

test("repeated start has one watchdog and requests wait for preceding poll", async () => {
  const slow = deferred();
  const f = setup([slow.promise, response("Next")]);
  f.client.start(); f.client.start();
  assert.equal(f.calls.length, 1); assert.equal(f.watches.size, 1);
  assert.equal([...f.jobs.values()].filter(job => job.delay === 150).length, 0);
  slow.resolve(response("First")); await flush();
  f.fire(150); await flush(); assert.equal(f.text.textContent, "Next");
  f.client.stop(); f.client.start(); await flush();
  assert.equal(f.watches.size, 1);
  f.client.stop();
});

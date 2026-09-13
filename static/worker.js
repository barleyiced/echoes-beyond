/* The browser backend: CPython on WASM running the real engine.
 *
 * This is not a port. It boots Pyodide, unpacks the same engine/ and
 * web/router.py the local FastAPI app serves, and answers postMessage calls
 * through the very same dispatch(). One scoring implementation, two transports
 * -- see WEB-PLAN.md.
 *
 * A worker rather than the main thread because boot is multi-second and some
 * rankings are not instant; on the main thread that freezes the page.
 */

// A module worker, not a classic one: Chrome 151 rejects `new Worker(url)`
// without { type: 'module' } outright ("Classic web workers are not supported"),
// so importScripts() is not an option and Pyodide is loaded as an ES module.
import { loadPyodide } from './dist/pyodide/pyodide.mjs';

const DIST = new URL('./dist/', self.location.href);

let py = null;
let callPython = null;

const post = (msg) => self.postMessage(msg);
const status = (text, detail = '') => post({ type: 'status', text, detail });

// IndexedDB <-> the in-memory FS. syncfs(true) loads, syncfs(false) persists.
// Spike 0c confirmed os.replace works on IDBFS, so engine/state.py runs
// unmodified -- but the Python side cannot flush itself, so every write route
// has to be followed by a push from here.
const syncfs = (fromIDB) =>
  new Promise((res, rej) => py.FS.syncfs(fromIDB, (e) => (e ? rej(e) : res())));

// Routes that touch runs/. Anything here needs a flush to IndexedDB afterwards,
// or the run is lost on close -- the exact failure engine/state.py's atomic
// writes exist to prevent, reintroduced one layer up.
const WRITES = new Set(['/api/run/save', '/api/run/restore']);

async function boot() {
  status('Downloading Python', 'about 13 MB, once. It caches afterwards');
  py = await loadPyodide({ indexURL: new URL('pyodide/', DIST).href });

  status('Loading packages');
  await py.loadPackage(['pyyaml']);

  status('Unpacking the dataset');
  const resp = await fetch(new URL('python.zip', DIST));
  if (!resp.ok) throw new Error(`python.zip: ${resp.status}. Run: python -m data.site`);
  const buf = await resp.arrayBuffer();
  py.FS.mkdirTree('/project');
  py.unpackArchive(buf, 'zip', { extractDir: '/project' });

  status('Opening your saved runs');
  py.FS.mkdirTree('/project/runs');
  py.FS.mount(py.FS.filesystems.IDBFS, {}, '/project/runs');
  await syncfs(true);

  status('Starting the engine');
  callPython = py.runPython(`
import json, sys
sys.path.insert(0, "/project")

from web.router import dispatch, ApiError

def _call(path, body_json):
    """One route, in and out as JSON text.

    Crossing the boundary as text rather than as proxied objects keeps the
    contract identical to the HTTP shim's -- the frontend cannot tell which
    backend answered, which is the whole point.
    """
    try:
        return json.dumps({"ok": True, "data": dispatch(path, json.loads(body_json))})
    except ApiError as e:
        return json.dumps({"ok": False, "status": e.status, "detail": e.detail})
    except Exception as e:                       # never let the worker die
        return json.dumps({"ok": False, "status": 500,
                           "detail": f"{type(e).__name__}: {e}"})

_call
`);

  // Warm the dataset now rather than on the user's first click.
  status('Reading the dataset');
  callPython('/api/meta', '{}');

  post({ type: 'ready' });
}

self.onmessage = async (ev) => {
  const { id, path, body } = ev.data || {};
  if (!id) return;
  try {
    if (!callPython) throw new Error('the engine is still starting');
    const raw = callPython(path, JSON.stringify(body ?? {}));
    const out = JSON.parse(raw);
    if (out.ok && WRITES.has(String(path).split('?')[0])) await syncfs(false);
    post({ id, ...out });
  } catch (e) {
    post({ id, ok: false, status: 500, detail: String(e && e.message || e) });
  }
};

boot().catch((e) => post({ type: 'fatal', detail: String(e && e.message || e) }));

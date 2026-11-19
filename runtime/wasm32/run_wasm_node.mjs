import fs from "node:fs";

const wasmPath = process.argv[2];
if (!wasmPath) {
  console.error("usage: node run_wasm_node.mjs <module.wasm>");
  process.exit(2);
}

class WasmExit {
  constructor(code) { this.code = code; }
}

const bytes = fs.readFileSync(wasmPath);
const t0 = performance.now();
let memory = null;
let framesPresented = 0;
let lastW = 0, lastH = 0, nonZeroPx = 0;
let waitTicks = 0;

function readStr(ptr) {
  if (!ptr || !memory) return "";
  const mem = new Uint8Array(memory.buffer);
  let end = ptr;
  while (end < mem.length && mem[end] !== 0) end++;
  return Buffer.from(mem.subarray(ptr, end)).toString("utf8");
}

const imports = {
  env: {
    wasm_host_log(stream, ptr, len) {
      if (!memory || len <= 0) return;
      const mem = new Uint8Array(memory.buffer, ptr, len);
      const txt = Buffer.from(mem).toString("utf8");
      if (stream === 2) process.stderr.write(txt);
      else process.stdout.write(txt);
    },
    wasm_host_now_ms() {
      return performance.now() - t0;
    },
    wasm_host_exit(code) {
      throw new WasmExit(code | 0);
    },
    wasm_host_present_fb(rgbaPtr, w, h, _titlePtr) {
      framesPresented++;
      lastW = w;
      lastH = h;
      if (rgbaPtr && w > 0 && h > 0 && memory) {
        const px = new Uint32Array(memory.buffer, rgbaPtr, w * h);
        let nz = 0;
        const first = px[0];
        for (let i = 0; i < px.length; i++) {
          if (px[i] !== first && px[i] !== 0) nz++;
        }
        if (nz > nonZeroPx) nonZeroPx = nz;
      }
    },
    wasm_host_wait_event(_timeoutMs, outEvPtr) {
      waitTicks++;
      const view = new Int32Array(memory.buffer, outEvPtr, 5);
      if (waitTicks >= 4) {
        view[0] = -1; // WM_QUIT after initial frames + timer ticks
        return -1;
      }
      view[0] = 0; // timer / idle tick
      view[1] = 0; view[2] = 0; view[3] = 0; view[4] = 0;
      return 0;
    },
    wasm_sin: Math.sin,
    wasm_cos: Math.cos,
    wasm_tan: Math.tan,
    wasm_asin: Math.asin,
    wasm_acos: Math.acos,
    wasm_atan: Math.atan,
    wasm_atan2: Math.atan2,
    wasm_pow: Math.pow,
    wasm_exp: Math.exp,
    wasm_log: Math.log,
  },
};

const { instance } = await WebAssembly.instantiate(bytes, imports);
memory = instance.exports.memory;

let exitCode = 0;
try {
  exitCode = instance.exports.wasm_init() | 0;
} catch (err) {
  if (err instanceof WasmExit) {
    exitCode = err.code;
  } else {
    console.error("[wasm] runtime trap:", err);
    process.exit(3);
  }
}

process.stderr.write(
  `[wasm] rendered ${framesPresented} frame(s), fb=${lastW}x${lastH}, distinct_pixels=${nonZeroPx}, exit=${exitCode}\n`
);
process.exit(exitCode);

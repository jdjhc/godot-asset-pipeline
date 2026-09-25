/**
 * Narrow transport through the installed, official tripo-cli package.
 * No credential parsing here: TripoClient owns authentication. Version/exports
 * are checked before constructing a client. Generation is NEVER retried.
 * `validate` only invokes pure request builders; it cannot upload or submit.
 * Verified against tripo-cli 0.5.1 and Tripo V3 docs on 2026-09-25.
 */
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(process.argv[2] || '.');
const packageInfo = JSON.parse(await readFile(resolve(root, 'package.json'), 'utf8'));
if (packageInfo.name !== 'tripo-cli' || packageInfo.version !== '0.5.1') {
  throw new Error('Unsupported official CLI version; transport requires review.');
}
const load = (path) => import(pathToFileURL(resolve(root, path)).href);
const { TripoClient } = await load('dist/core/client.js');
const { resolveInputValue } = await load('dist/core/task-service.js');
const { installProxyFromEnv } = await load('dist/core/proxy.js');
const builders = await load('dist/core/requests.js');
for (const name of ['buildTextToImage', 'buildImageToImage', 'buildImageToMultiview', 'buildMultiviewToModel']) {
  if (typeof builders[name] !== 'function') throw new Error('Official CLI request interface changed.');
}
if (typeof TripoClient !== 'function' || typeof TripoClient.prototype.createTask !== 'function' ||
    typeof TripoClient.prototype.sendOnce !== 'function' || typeof resolveInputValue !== 'function') {
  throw new Error('Official CLI client interface changed.');
}

let source = '';
for await (const part of process.stdin) source += part;
const spec = JSON.parse(source);
if (!['validate', 'submit'].includes(spec.operation)) throw new Error('Unsupported transport operation.');
const files = spec.files || [];
const viewOrder = ['front', 'left', 'back', 'right'];

function build(values) {
  const params = { ...(spec.params || {}) };
  switch (spec.stage) {
    case 'text-to-image':
      return builders.buildTextToImage(spec.prompt, { ...params, model: spec.model });
    case 'image-to-image':
      // The official endpoint's multi-reference shape is inputs (without
      // input). The official builder passes extra API fields through; undefined
      // input is omitted when serializing the HTTP JSON body.
      return builders.buildImageToImage(values.length === 1 ? values[0] : undefined,
        { ...params, model: spec.model, ...(spec.prompt ? { prompt: spec.prompt } : {}),
          ...(values.length > 1 ? { inputs: values } : {}) });
    case 'image-to-multiview':
      return builders.buildImageToMultiview(values[0], params);
    case 'multiview-to-model': {
      const views = {};
      files.forEach((file, index) => {
        if (!viewOrder.includes(file.role) || views[file.role]) throw new Error('Invalid or duplicate view role.');
        views[file.role] = values[index];
      });
      return builders.buildMultiviewToModel(views, spec.model, params);
    }
    default:
      throw new Error('Unsupported generation stage.');
  }
}

let submissionStarted = false;
try {
  // Validate before uploads or client construction. CLI normalization must agree
  // with the preview: unexpected parameter rewriting is a safe local failure.
  const placeholders = files.map((file) => `<upload:${file.path}>`);
  const checked = build(placeholders);
  if (JSON.stringify(checked.request.payload) !== JSON.stringify(spec.expected_payload)) {
    // Object key order is immaterial, including keys added by request builders.
    const stable = (value) => Array.isArray(value) ? value.map(stable) :
      value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])])) : value;
    if (JSON.stringify(stable(checked.request.payload)) !== JSON.stringify(stable(spec.expected_payload))) {
      throw new Error('Official CLI normalization differs from reviewed preview.');
    }
  }
  if (spec.operation === 'validate') {
    process.stdout.write(JSON.stringify({ valid: true, request: checked.request, warnings: checked.warnings }) + '\n');
  } else {
    await installProxyFromEnv();
    const client = new TripoClient({ maxRetries: 0 });
    if (client.maxRetries !== 0) throw new Error('Cannot guarantee a single submission attempt.');
    const values = [];
    for (const file of files) values.push((await resolveInputValue(client, file.path)).value);
    const built = build(values);
    submissionStarted = true;
    const taskId = await client.createTask(built.request.endpoint, built.request.payload);
    if (typeof taskId !== 'string' || !taskId) throw new Error('Submission response lacks a task identifier.');
    process.stdout.write(JSON.stringify({ task_id: taskId, type: built.taskType, status: 'queued' }) + '\n');
  }
} catch (error) {
  // Preserve only support/debug identifiers. Never return raw API error text,
  // profiles, response bodies, headers, or credential hints. Unknown identifier
  // formats are omitted rather than copied into persistent job records.
  const status = error?.httpStatus;
  const apiCode = error?.apiCode;
  const requestId = error?.requestId;
  const validRequestId = typeof requestId === 'string' &&
    /^(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}|req_[A-Za-z0-9]{8,64})(?![\s\S])/.test(requestId);
  const diagnostics = {
    ...(Number.isInteger(status) && status >= 100 && status <= 599 ? { http_status: status } : {}),
    ...(Number.isInteger(apiCode) && apiCode > 0 && apiCode <= 999999 ? { api_code: apiCode } : {}),
    ...(validRequestId ? { request_id: requestId } : {}),
  };
  const rejected = [400, 401, 402, 403, 404, 413, 422, 429].includes(status);
  process.stdout.write(JSON.stringify({ failure_phase: !submissionStarted ? 'pre_submit' : rejected ? 'rejected' : 'uncertain',
    ...diagnostics }) + '\n');
  process.stderr.write('Tripo transport did not return a confirmed result.\n');
  process.exitCode = 1;
}

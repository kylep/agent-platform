#!/usr/bin/env node
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
const root=fileURLToPath(new URL('../../..',import.meta.url));
const local=join(root,'services/backend/.venv/bin/python');
const python=process.env.AP_VERIFY_PYTHON || (existsSync(local) ? local : 'python3');
const result=spawnSync(python,[join(root,'bin/build-designer-parity'),'--fixtures'],{cwd:root,stdio:'inherit'});
if(result.error) throw result.error;
if(result.status) console.error('Designer parity needs Python 3.12+ with the backend’s pinned fonttools and uharfbuzz packages. Set AP_VERIFY_PYTHON to that interpreter.');
process.exitCode=result.status ?? 1;

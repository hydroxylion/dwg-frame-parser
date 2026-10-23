// 前端测试汇总运行器：一次跑完全部 test_*.js，汇总通过/失败/跳过。
// 用法: npm test   或   node run_frontend_tests.js
// 特殊说明: SKIP 的用例（如未装 jsdom）不计入失败，会在末尾单独列出。
const { spawnSync } = require('child_process');
const path = require('path');

// 固定顺序，便于对比历史输出；新增用例文件时在此追加
const FILES = [
    'test_judge_logic.js',
    'test_single_frame_tip.js',
    'test_tip_interaction.js',
    'test_xlsx_export.js',
    'test_xlsx_e2e.js',
    'test_layout_col_width.js',
    'test_delete_script_logic.js',
    'test_xref_warning.js',
];

// 各用例文件的汇总行格式不统一（"结果: N 通过, M 失败" / "结果：N 通过 / M 失败"），
// 统一按中文关键词抓数字，避免各文件各自改动输出格式时汇总失效。
function parseSummary(out) {
    let pass = 0, fail = 0;
    for (const m of out.matchAll(/(\d+)\s*通过/g)) pass = Math.max(pass, +m[1]);
    for (const m of out.matchAll(/(\d+)\s*失败/g)) fail = Math.max(fail, +m[1]);
    return { pass, fail };
}

const results = [];
let totalPass = 0, totalFail = 0;
const skipped = [];

for (const f of FILES) {
    const r = spawnSync(process.execPath, [path.join(__dirname, f)], {
        cwd: __dirname, encoding: 'utf8',
    });
    const out = (r.stdout || '') + (r.stderr || '');
    const isSkip = /SKIP/.test(out);
    if (isSkip) {
        skipped.push(f);
        console.log(`SKIP  ${f}`);
        results.push({ f, pass: 0, fail: 0, skip: true });
        continue;
    }
    const { pass, fail } = parseSummary(out);
    totalPass += pass;
    totalFail += fail;
    const mark = fail === 0 ? 'PASS' : 'FAIL';
    console.log(`${mark}  ${f.padEnd(28)} ${String(pass).padStart(4)} 通过  ${fail} 失败`);
    results.push({ f, pass, fail, skip: false });
}

console.log('\n' + '='.repeat(56));
if (skipped.length) {
    console.log(`跳过 ${skipped.length} 个: ${skipped.join(', ')}（多为未安装 jsdom，npm install 后重跑）`);
}
console.log(`合计 ${totalPass} 通过 / ${totalFail} 失败（共 ${FILES.length} 个用例文件，跳过 ${skipped.length} 个）`);
console.log('='.repeat(56));

process.exit(totalFail === 0 ? 0 : 1);

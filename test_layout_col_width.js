// 专项验证：布局分布列的宽度是否随内容自适应（针对用户反馈的问题）
const fs = require('fs'), path = require('path');
const DIR = 'C:/Users/hui_ou/PycharmProjects/dwg-frame-parser';
const { JSDOM } = require('jsdom');
const html = fs.readFileSync(path.join(DIR, 'index.html'), 'utf8');
const lib = fs.readFileSync(path.join(DIR, 'vendor', 'xlsx-style.bundle.js'), 'utf8');
const app = fs.readFileSync(path.join(DIR, 'app.js'), 'utf8');

let pass = 0, fail = 0;
function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
    if (!ok) console.log(`       实际: ${JSON.stringify(actual)}\n       期望: ${JSON.stringify(expected)}`);
    ok ? pass++ : fail++;
}

function run(recs) {
    const d = new JSDOM(html, { url: 'http://127.0.0.1:5000/', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = d.window;
    w.eval(lib);
    let cap = null;
    w.URL.createObjectURL = (b) => { cap = b; return 'blob:m'; };
    w.URL.revokeObjectURL = () => {};
    const oc = w.document.createElement.bind(w.document);
    w.document.createElement = (t) => { const e = oc(t); if (String(t).toLowerCase() === 'a') e.click = function () {}; return e; };
    const driver = [
        'records.length=0;',
        'var __r=' + JSON.stringify(recs) + ';',
        '__r.forEach(function(r,i){records.push(Object.assign({id:String(i),frameConf:90,scaleConf:85,expected:"A1"},r));});',
        'window.__tbl=buildExportTable(records);',
        'window.__w=computeColWidths(window.__tbl.headers, window.__tbl.rows);',
        'window.__dist=buildFramesLayoutText;',
        'exportXlsx(records);',
    ].join('\n');
    w.eval(app + '\n' + driver);
    return { w, cap, tbl: w.__tbl, widths: w.__w };
}

const mk = (name, lc, fc, fm) => ({
    name, frameCount: fc, w: 841, h: 594,
    framesMeta: fm || Array.from({ length: fc }, () => ({ w: 841, h: 594, layout: '模型空间' })),
    layoutCounts: lc, actualResult: '标准', match: 'yes', note: '',
});

console.log('=== 场景A：布局分布为空（旧记录 / 手动输入）===');
{
    const { tbl, widths } = run([mk('一层平面图', null, 1, [])]);
    const txt = tbl.rows[0][3];
    console.log('   布局分布内容:', JSON.stringify(txt));
    console.log('   布局分布列宽:', widths[3]);
    check('空内容时列宽 = 表头宽度 + 边距', Math.round(widths[3] * 100) / 100, Math.round((8 + 1.6) * 100) / 100);
}

console.log('\n=== 场景B：单个短布局 ===');
{
    const { tbl, widths } = run([mk('一层平面图', { '模型空间': 1 }, 1)]);
    console.log('   内容:', JSON.stringify(tbl.rows[0][3]));
    console.log('   列宽:', Math.round(widths[3] * 100) / 100);
    check('短布局列宽按内容', Math.round(widths[3] * 100) / 100, Math.round((10 + 1.6) * 100) / 100);
}

console.log('\n=== 场景C：多布局 + 长布局名（关键场景）===');
{
    const lc = { '模型空间': 12, '布局 "A1-一层平面布置图-出图窗口"': 2, '布局 "Layout2"': 1 };
    const { tbl, widths } = run([mk('世欧澜山', lc, 15)]);
    const txt = tbl.rows[0][3];
    console.log('   内容:');
    String(txt).split('\n').forEach(l => console.log('     ' + JSON.stringify(l)));
    console.log('   列宽:', Math.round(widths[3] * 100) / 100);
    // 最长行 = 布局 "A1-一层平面布置图-出图窗口"×2
    const longest = String(txt).split('\n').reduce((a, b) => (b.length > a.length ? b : a));
    console.log('   最长行:', JSON.stringify(longest), ' 字符数:', [...longest].length);
    check('长布局名列宽明显大于短布局场景', widths[3] > 20, true);
    check('列宽足以容纳最长行（按字宽估算）', widths[3] > [...longest].length, true);
}

console.log('\n=== 场景D：列宽确实随内容变化（不同内容 → 不同列宽）===');
{
    const a = run([mk('图', { '模型空间': 1 }, 1)]).widths[3];
    const b = run([mk('图', { '模型空间': 12, '布局 "A1-一层平面布置图-出图窗口"': 2, '布局 "Layout2"': 1 }, 15)]).widths[3];
    console.log(`   短内容列宽=${Math.round(a * 100) / 100}, 长内容列宽=${Math.round(b * 100) / 100}`);
    check('长内容 → 更宽的列（这就是"自适应"）', b > a, true);
}

console.log('\n=== 场景E：导出文件里布局分布列的实际宽度 ===');
(async () => {
    const lc = { '模型空间': 12, '布局 "A1-一层平面布置图-出图窗口"': 2 };
    const { w, cap, widths } = run([mk('世欧澜山', lc, 14)]);
    const buf = Buffer.from(await cap.arrayBuffer());
    const XLSX = w.eval('XLSX');
    const wb = XLSX.read(buf, { type: 'buffer', cellStyles: true, cellNF: true });
    const ws = wb.Sheets[wb.SheetNames[0]];
    const cols = ws['!cols'];
    const H = XLSX.utils.sheet_to_json(ws, { header: 1 })[0];
    console.log('   读回文件各列宽:');
    cols.forEach((c, i) => console.log('     ' + String(H[i]).padEnd(14) + ' -> ' + c.wch));
    check('布局分布列宽已写入文件', Math.abs(cols[3].wch - widths[3]) < 0.1, true);
    check('布局分布列宽 > 表头宽度（不是固定值）', cols[3].wch > cols[0].wch, true);

    console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
    process.exit(fail === 0 ? 0 : 1);
})();

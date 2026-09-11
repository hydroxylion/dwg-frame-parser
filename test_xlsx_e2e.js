// 端到端：在 jsdom 里真实加载 index.html（含 vendor/xlsx 库 + app.js），
// 点击「导出 Excel」按钮，验证真的产出了带自适应列宽的 .xlsx。
// 运行: node test_xlsx_e2e.js
const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
    if (!ok) console.log(`       实际: ${JSON.stringify(actual)}\n       期望: ${JSON.stringify(expected)}`);
    ok ? pass++ : fail++;
}

let JSDOM;
try {
    ({ JSDOM } = require('jsdom'));
} catch (_) {
    console.log('SKIP  test_xlsx_e2e.js —— 未安装 jsdom');
    process.exit(0);
}

const DIR = __dirname;
const html = fs.readFileSync(path.join(DIR, 'index.html'), 'utf8');
const xlsxLib = fs.readFileSync(path.join(DIR, 'vendor', 'xlsx-style.bundle.js'), 'utf8');

// 拦截写文件：SheetJS 在浏览器里走 Blob + a.click()，我们把产物截下来
let written = null;

const dom = new JSDOM(html, {
    url: 'http://127.0.0.1:5000/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
});
const { window } = dom;

// 把 XLSX 库注入到 window（模拟 <script src="vendor/xlsx-style.bundle.js">）
window.eval(xlsxLib);
check('window.XLSX 可用', window.eval('typeof XLSX'), 'object');
// 用的是 xlsx-js-style（SheetJS 0.18.5 的带样式分支），不是原版 SheetJS
check('XLSX 版本可读', window.eval('XLSX.version'), '0.18.5');
check('带样式能力（style_version 存在）', typeof window.eval('XLSX.style_version'), 'string');

// 拦截 URL.createObjectURL + a.click，捕获导出的 blob
const captured = [];
window.URL.createObjectURL = function (blob) {
    captured.push(blob);
    return 'blob:mock/' + captured.length;
};
window.URL.revokeObjectURL = function () {};
const origCreate = window.document.createElement.bind(window.document);
window.document.createElement = function (tag) {
    const el = origCreate(tag);
    if (String(tag).toLowerCase() === 'a') {
        el.click = function () { /* 不真的下载 */ };
    }
    return el;
};

// app.js 用 let records 声明，顶层 let 不会挂到 window 上，
// 所以把 app.js 与测试代码拼成同一段脚本来执行（共享同一作用域）
const appSrc = fs.readFileSync(path.join(DIR, 'app.js'), 'utf8');
const driver = `
// ---- 测试驱动：与 app.js 同作用域 ----
records.length = 0;
records.push(
    { id: '1', name: '一层平面图', frameCount: 1, w: 841, h: 594,
      framesMeta: [{ w: 841, h: 594, layout: '模型空间' }],
      layoutCounts: { '模型空间': 1 }, actualResult: '标准图框',
      frameConf: 92, scaleConf: 88, expected: 'A1', match: 'yes', note: '' },
    { id: '2', name: '地下室通风与防排烟平面布置图（含设备表）', frameCount: 3, w: 841, h: 594,
      framesMeta: [
        { w: 841, h: 594, layout: '模型空间' },
        { w: 594, h: 420, layout: '模型空间' },
        { w: 420, h: 297, layout: '布局 "Sheet1"' }
      ],
      layoutCounts: { '模型空间': 2, '布局 "Sheet1"': 1 },
      actualResult: '标准图框', frameConf: 90, scaleConf: 85, expected: 'A1', match: 'yes', note: '多图框' }
);
window.__exportBtn = document.getElementById('exportXlsxBtn');
window.__recordsOk = records.length === 2;
window.__computeColWidths = computeColWidths;
`;
try {
    window.eval(appSrc + '\n' + driver);
} catch (e) {
    console.log('app.js 执行失败:', e.message);
    process.exit(1);
}
console.log('   app.js 已在 jsdom 中加载');
check('测试记录已注入', window.__recordsOk, true);

console.log('\n=== 点击「导出 Excel」按钮 ===');
const btn = window.__exportBtn;
check('导出 Excel 按钮存在', !!btn, true);
btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('产生了导出文件（blob）', captured.length, 1);

const blob = captured[0];
const blobType = blob.type || '';
console.log('   blob MIME:', blobType);
check('导出的是 xlsx MIME 类型', /spreadsheetml/.test(blobType), true);

// jsdom 的 Blob 支持 arrayBuffer()
(async () => {
    const buf = Buffer.from(await blob.arrayBuffer());
    console.log('   导出文件大小:', buf.length, 'bytes');
    check('导出文件非空', buf.length > 1000, true);
    check('文件头是 ZIP 魔数（真 xlsx）', buf.subarray(0, 4).toString('hex'), '504b0304');

    // 用库读回导出的文件，验证列宽
    const XLSX = window.eval('XLSX');
    const wb = XLSX.read(buf, { type: 'buffer', cellStyles: true, cellNF: true });
    const ws = wb.Sheets[wb.SheetNames[0]];
    console.log('   工作表名:', wb.SheetNames[0]);
    check('工作表名', wb.SheetNames[0], '图框检测记录');

    const rows = XLSX.utils.sheet_to_json(ws, { header: 1 });
    console.log('   表头:', rows[0].join(' | '));
    check('表头列数 14', rows[0].length, 14);
    check('数据行数 2', rows.length - 1, 2);

    const cols = ws['!cols'];
    console.log('   列宽:', cols.map(c => c.wch).join(', '));
    check('列宽已写入文件', Array.isArray(cols) && cols.length === 14, true);
    check('Excel 标记自定义宽度', cols.every(c => c.customwidth === '1'), true);

    // 关键：长图名那一列必须明显宽于短列 —— 这就是"按最大宽度适配"
    const nameW = cols[1].wch;
    const seqW = cols[0].wch;
    console.log(`   图纸名称列宽=${nameW}, 序号列宽=${seqW}`);
    check('图纸名称列明显更宽（自适应生效）', nameW > seqW * 3, true);
    check('列宽不超过上限 42', cols.every(c => c.wch <= 42), true);
    check('列宽不低于下限 6', cols.every(c => c.wch >= 6), true);

    // 多行单元格：图框尺寸列应含换行
    const dimText = String(rows[2][4]);
    console.log('   图框尺寸单元格:', JSON.stringify(dimText));
    check('图框尺寸单元格多行', dimText.split('\n').length >= 2, true);

    // ---- 样式必须真的写进文件（SheetJS 免费版会丢弃 cell.s，这是曾经的坑）----
    console.log('');
    console.log('=== 样式是否真的写入文件 ===');
    const zlib = require('zlib');
    function unzipAll(b) {
        const files = {};
        let pos = 0;
        while (pos < b.length - 4) {
            if (b.readUInt32LE(pos) === 0x04034b50) {
                const method = b.readUInt16LE(pos + 8);
                const csize = b.readUInt32LE(pos + 18);
                const nlen = b.readUInt16LE(pos + 26);
                const elen = b.readUInt16LE(pos + 28);
                const name = b.slice(pos + 30, pos + 30 + nlen).toString();
                const ds = pos + 30 + nlen + elen;
                const raw = b.slice(ds, ds + csize);
                files[name] = method === 8 ? zlib.inflateRawSync(raw).toString() : raw.toString();
                pos = ds + csize;
            } else pos++;
        }
        return files;
    }
    const files = unzipAll(buf);
    const sheetXml = files['xl/worksheets/sheet1.xml'] || '';
    const stylesXml = files['xl/styles.xml'] || '';

    check('styles.xml 存在', !!stylesXml, true);
    check('单元格带样式索引 s=', /<c r="A2"[^>]*\ss="\d+"/.test(sheetXml), true);
    check('样式含 wrapText（多行文本能否换行的关键）', stylesXml.includes('wrapText'), true);
    check('样式含加粗表头', stylesXml.includes('<b/>'), true);
    check('样式含表头灰底 F0F3F8', stylesXml.toUpperCase().includes('F0F3F8'), true);

    // 布局分布列 / 图框尺寸列必须都套上 wrapText，否则多行会被挤成一行
    const a2Match = sheetXml.match(/<c r="E2"[^>]*\ss="(\d+)"/);
    console.log('   E2 单元格样式索引:', a2Match ? a2Match[1] : '(无)');
    check('数据单元格有样式索引', !!a2Match, true);

    console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
    process.exit(fail === 0 ? 0 : 1);
})();

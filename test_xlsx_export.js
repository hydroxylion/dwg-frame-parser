// Excel 导出（列宽自适应）测试
// 运行: node test_xlsx_export.js
// 关键点：不是"看代码有没有写 !cols"，而是真生成一个 .xlsx、再读回来，
// 验证列宽确实随内容变化，且中文按 2 字符宽计算。
const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
    if (!ok) console.log(`       实际: ${JSON.stringify(actual)}\n       期望: ${JSON.stringify(expected)}`);
    ok ? pass++ : fail++;
}
function checkRange(name, actual, lo, hi) {
    const ok = actual >= lo && actual <= hi;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  (${actual}, 期望 ${lo}~${hi})`);
    ok ? pass++ : fail++;
}

// ---- 从 app.js 抽取被测的纯函数（同源复制，保证与线上一致）----
const src = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
function extractFn(name) {
    const start = src.indexOf(`function ${name}(`);
    if (start === -1) throw new Error(`未找到函数 ${name}`);
    // 从函数名开始做花括号配平，截出完整函数体
    let i = src.indexOf('{', start), depth = 0, end = -1;
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
    }
    return src.slice(start, end);
}
// 抽取所有相关的常量与查表（宽度表也要一并取出，否则函数内引用不到）
function extractConst(name) {
    // 匹配 const NAME = ...; 支持对象字面量与数字（跨行）
    const re = new RegExp(`const ${name} = `);
    const start = src.search(re);
    if (start === -1) throw new Error(`未找到常量 ${name}`);
    // 从 '=' 之后开始，做括号配平找到语句结束的分号
    let i = src.indexOf('=', start) + 1;
    let depth = 0;
    for (; i < src.length; i++) {
        const c = src[i];
        if (c === '{' || c === '[' || c === '(') depth++;
        else if (c === '}' || c === ']' || c === ')') depth--;
        else if (c === ';' && depth === 0) { i++; break; }
    }
    return src.slice(start, i);
}

const consts = [
    extractConst('XLSX_COL_WIDTH_MIN'),
    extractConst('XLSX_COL_WIDTH_MAX'),
    extractConst('XLSX_COL_WIDTH_PAD'),
    extractConst('XLSX_MDW'),
    extractConst('XLSX_NARROW_PX'),
    extractConst('XLSX_WIDE_PX'),
    extractConst('XLSX_MDW_PX'),
].join('\n');
const fns = [
    consts,
    extractFn('displayWidth'),
    extractFn('textDisplayWidth'),
    extractFn('computeColWidths'),
].join('\n');

const mod = {};
new Function('mod', fns + '\nmod.displayWidth=displayWidth; mod.textDisplayWidth=textDisplayWidth; mod.computeColWidths=computeColWidths;')(mod);
const { displayWidth, textDisplayWidth, computeColWidths } = mod;

console.log('=== 1. 字符显示宽度 ===');
check('单个汉字 宽 2', displayWidth('图'), 2);
check('单个 ASCII 宽 1', displayWidth('a'), 1);
check('全角逗号 宽 2', displayWidth('，'), 2);
check('"（" 宽 2', displayWidth('（'), 2);
check('emoji 宽 2', displayWidth('🖼'), 2);
check('"图框尺寸" 共 8', textDisplayWidth('图框尺寸'), 8);
check('"841×594" 共 7（× 视作 1）', textDisplayWidth('841×594'), 7);
check('空串 0', textDisplayWidth(''), 0);
check('null 0', textDisplayWidth(null), 0);

// 窄字符按真实字宽折算（<1）：这是本次从"一律算 1"改为字宽表的核心收益
//   空格 3.7/7 ≈ 0.529，直引号 4.4/7 ≈ 0.629，连字符 4.4/7 ≈ 0.629
check('空格 窄于 1 个 0 字符宽', Math.round(displayWidth(' ') * 1000) / 1000, Math.round((3.7 / 7) * 1000) / 1000);
check('直引号 窄于 1 个 0 字符宽', Math.round(displayWidth('"') * 1000) / 1000, Math.round((4.4 / 7) * 1000) / 1000);
check('连字符 窄于 1 个 0 字符宽', Math.round(displayWidth('-') * 1000) / 1000, Math.round((4.4 / 7) * 1000) / 1000);
check('宽字符 W 大于 1 个 0 字符宽', displayWidth('W') > 1, true);

console.log('\n=== 2. 列宽按内容取最大值 ===');
const H = ['序号', '图纸名称', '图框尺寸'];
// 序号列内容窄、表头也窄 → 兜底到 MIN
// 图纸名称列某行很长 → 跟着变宽
const rows = [
    [1, '一层平面图', '841×594'],
    [2, '地下室通风与防排烟平面布置图（含设备表）', '841×594\n594×420 ×3\n420×297 ×8'],
    [3, '支座', '297×210'],
];
const widths = computeColWidths(H, rows);
console.log('   列宽:', widths.map(w => Math.round(w * 100) / 100));
checkRange('序号列宽度夹到 MIN', widths[0], 6, 12);
check('图纸名称列 > 图框尺寸列', widths[1] > widths[2], true);
// 名称列最长行「地下室通风与防排烟平面布置图（含设备表）」= 20 汉字 = 40 宽
//   40 + 1.6 = 41.6，刚好在 MAX(42) 之内，故不触发封顶
check('图纸名称列按内容自适应（40 + 1.6 = 41.6）',
    Math.round(widths[1] * 100) / 100, 41.6);
checkRange('图框尺寸列按最长行（"420×297 ×8"）', widths[2], 10, 14);
check('表头参与比较（序号列不会窄于表头）', widths[0] >= 6, true);

console.log('\n=== 2b. 未超长时精确等于「最长内容 + 边距」===');
const wShort = computeColWidths(['名称'], [['一层平面图'], ['支座']]);
// 「一层平面图」= 5 汉字 = 10 宽 → 10 + 边距(1.6) = 11.6
check('短内容列 = 最长内容宽度 + 边距', Math.round(wShort[0] * 100) / 100, Math.round((10 + 1.6) * 100) / 100);

console.log('\n=== 3. 多行单元格取最长行 ===');
// "这是一行明显更长的中文内容" = 13 个汉字 = 26 宽 → 26 + 1.6 = 27.6（未触顶）
// 若错误地"累加所有行"会得到远大于此的值，这里恰好能区分
const w1 = computeColWidths(['X'], [['短\n这是一行明显更长的中文内容\n中']]);
check('多行取最长行而非累加（26 + 1.6 = 27.6）', Math.round(w1[0] * 100) / 100, 27.6);
const w1b = computeColWidths(['X'], [['短\n中等长度\n中']]);
check('多行取最长行（"中等长度"=4 字=8 宽 → 9.6）', Math.round(w1b[0] * 100) / 100, 9.6);

console.log('\n=== 4. 超长内容夹到 MAX（防止一屏放不下）===');
const w2 = computeColWidths(['备注'], [['备'.repeat(200)]]);
check('超长列夹到 42', w2[0], 42);

console.log('\n=== 5. 真生成 .xlsx 并读回列宽（端到端）===');
let XLSX;
try {
    XLSX = require('xlsx');
} catch (_) {
    try {
        const libPath = path.join(__dirname, 'vendor', 'xlsx-style.bundle.js');
        const libSrc = fs.readFileSync(libPath, 'utf8');
        const m = { exports: {} };
        new Function('module', 'exports', libSrc + ';return module.exports;')(m, m.exports);
        XLSX = m.exports;
    } catch (e) {
        console.log('SKIP  未找到 xlsx 库:', e.message);
        console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
        process.exit(fail === 0 ? 0 : 1);
    }
}
console.log('   库版本:', XLSX.version);

// 用与 app.js exportXlsx 相同的方式构造工作表
const headers = ['序号', '图纸名称', '图框数', '布局分布', '图框尺寸'];
const dataRows = [
    [1, '一层平面图', 1, '模型空间×1', '841×594'],
    [2, '地下室通风与防排烟平面布置图（含设备表）', 13, '模型空间×13', '841×594\n594×420 ×3\n420×297 ×8'],
];
const aoa = [headers, ...dataRows];
const ws = XLSX.utils.aoa_to_sheet(aoa);
ws['!cols'] = computeColWidths(headers, dataRows).map(w => ({ wch: w }));

const wb = XLSX.utils.book_new();
XLSX.utils.book_append_sheet(wb, ws, '图框检测记录');

const outPath = path.join(__dirname, 'tmp_test_export.xlsx');
// 注意：沙箱里 eval 出来的库拿不到 Node 的 fs（writeFile 内部 require('fs') 会失败），
// 所以用 XLSX.write 取字节自己落盘——这同时也验证了浏览器端 writeFile 走的是同一套编码
const buf = XLSX.write(wb, { bookType: 'xlsx', type: 'buffer' });
fs.writeFileSync(outPath, buf);
check('生成了 .xlsx 文件', fs.existsSync(outPath), true);
const size = fs.statSync(outPath).size;
console.log('   文件大小:', size, 'bytes');
check('文件非空', size > 1000, true);

// 校验是合法的 xlsx（ZIP 魔数 PK\x03\x04）
const magic = fs.readFileSync(outPath).subarray(0, 4).toString('hex');
check('文件头是 ZIP 魔数（真 xlsx，非 CSV 改名）', magic, '504b0304');

// 读回来验证列宽真的被写进了文件。
// 注意：SheetJS 默认不解析列宽，须显式传 cellStyles:true 才会把 <cols> 读进 !cols
const wb2 = XLSX.read(buf, { type: 'buffer', cellStyles: true, cellNF: true });
const ws2 = wb2.Sheets[wb2.SheetNames[0]];
const cols = ws2['!cols'];
console.log('   读回列宽:', cols.map(c => c.wch));
check('列宽真的写进了文件（读回非空）', Array.isArray(cols) && cols.length === headers.length, true);
// xlsx 里列宽以像素值存储，回读时换算成 wch 并四舍五入到两位小数，
// 因此会有 ≤0.01 的量化误差（实测最大 0.07）。用 0.1 容差比较。
const roundtripOk = cols.every((c, i) => {
    const want = computeColWidths(headers, dataRows)[i];
    return Math.abs(c.wch - want) < 0.1;
});
check('读回的列宽与计算一致（±0.1 量化容差）', roundtripOk, true);
check('Excel 标记为自定义宽度（customwidth=1，不被自动重算覆盖）',
    cols.every(c => c.customwidth === '1'), true);
check('图纸名称列确实更宽（自适应生效）', cols[1].wch > cols[0].wch, true);
check('工作表里有数据', XLSX.utils.sheet_to_json(ws2).length, 2);
check('中文内容读回无损',
    XLSX.utils.sheet_to_json(ws2)[1]['图纸名称'],
    '地下室通风与防排烟平面布置图（含设备表）');
check('多行单元格换行符保留',
    String(XLSX.utils.sheet_to_json(ws2)[1]['图框尺寸']).split('\n').length, 3);

// 对比：不设 !cols 时列宽为空 —— 这正是 CSV/默认导出的状况
const wsNoCols = XLSX.utils.aoa_to_sheet(aoa);
check('不设列宽时 !cols 为空（对照）', wsNoCols['!cols'] === undefined, true);

fs.unlinkSync(outPath);
console.log('   已清理临时文件');

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);

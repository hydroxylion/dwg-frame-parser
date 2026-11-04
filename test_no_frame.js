// R134 无图框（0 框）状态：前端单元测试
// 运行: node test_no_frame.js
//
// 背景：固定环01--05.dwg 里没有任何标准图框，后端 collect 出的唯一候选又被剔除，
//   all_candidates 为空 → 旧后端在 force_max 分支 max([]) 崩掉，被包装成
//   「解析失败: max() iterable argument is empty」。图纸本来就没有图框，却被显示成
//   程序崩溃，这两件事必须分开。
// 本测试锁定四件事（都直接抽 app.js 里的真实函数来跑，不是照抄一份逻辑）：
//   ① 无图框判定只认「no_frame=true」或「0 框 + 无有效尺寸」，
//      不能把「有候选但 0 框」的图（一层.dwg 7530×2730）也吞掉
//   ② 无图框记录的判断结果不走 judgeSize，避免被说成"无效尺寸/非标准"
//   ③ 图框数筛选：0 框不能落进"单图框 (=1)"
//   ④ 结构一致：筛选复选框、类型统计档位、样式三处都要有 no_frame，
//      以后有人加了筛选项忘了同步 allTypes，这里会红

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
    if (!ok) console.log(`       实际: ${JSON.stringify(actual)}\n       期望: ${JSON.stringify(expected)}`);
    ok ? pass++ : fail++;
}

const src = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf-8');
const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf-8');
const css = fs.readFileSync(path.join(__dirname, 'style.css'), 'utf-8');

// 从 app.js 截出真实函数源码（花括号配平），再 eval 成可调用函数
function extractFn(name) {
    const start = src.indexOf(`function ${name}(`);
    if (start === -1) throw new Error(`未找到函数 ${name}`);
    let i = src.indexOf('{', start), depth = 0, end = -1;
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
    }
    return src.slice(start, end);
}
function extractConst(name) {
    const re = new RegExp(`const ${name} = `);
    const start = src.search(re);
    if (start === -1) throw new Error(`未找到常量 ${name}`);
    let i = src.indexOf('=', start) + 1, depth = 0;
    for (; i < src.length; i++) {
        const c = src[i];
        if (c === '{' || c === '[' || c === '(') depth++;
        else if (c === '}' || c === ']' || c === ')') depth--;
        else if (c === ';' && depth === 0) { i++; break; }
    }
    return src.slice(start, i);
}

const isNoFrameResult = eval('(' + extractFn('isNoFrameResult') + ')');
const noFrameResult = eval('(' + extractFn('noFrameResult') + ')');
const getRecordType = eval('(' + extractFn('getRecordType') + ')');
const TYPE_STATS_META = eval('(' + extractConst('TYPE_STATS_META').split('=')[1].trim().replace(/;$/, '') + ')');

console.log('\n=== 1. 无图框判定（isNoFrameResult） ===');

check('后端明确 no_frame=true → 无图框',
    isNoFrameResult({ no_frame: true, width: 0, height: 0, frame_count: 0 }), true);

check('有候选但 0 框（一层.dwg: 7530×2730, frame_count=0）→ 不是无图框',
    isNoFrameResult({ width: 7530, height: 2730, frame_count: 0 }), false);

check('有尺寸 + 0 框且显式 no_frame=false → 不是无图框',
    isNoFrameResult({ no_frame: false, width: 7530, height: 2730, frame_count: 0 }), false);

check('旧后端无 no_frame 字段、0 框 0 尺寸 → 按兜底判为无图框',
    isNoFrameResult({ width: 0, height: 0, frame_count: 0 }), true);

check('0 框但有尺寸（不可信的边缘数据）→ 不判无图框（宁可走 judgeSize 报警）',
    isNoFrameResult({ width: 11963, height: 11963, frame_count: 0 }), false);

check('正常图（1 框 841×594）→ 不是无图框',
    isNoFrameResult({ width: 841, height: 594, frame_count: 1 }), false);

check('null / undefined → false（失败路径不受影响）',
    [isNoFrameResult(null), isNoFrameResult(undefined)], [false, false]);

console.log('\n=== 2. 无图框记录的判断结果（noFrameResult） ===');

const nf = noFrameResult();
check('type = no_frame（不是 nonstandard / failed）', nf.type, 'no_frame');
check('label 明确写「未检测到图框」', nf.label, '未检测到图框');
check('detail 说明这是有效结果、不是解析失败',
    /0 个图框/.test(nf.detail) && /非解析失败/.test(nf.detail), true);
check('置信度为 0（没图框就是 0，不能编造）',
    [nf.confidenceFrame, nf.confidenceScale], [0, 0]);
check('getRecordType(无图框记录) → no_frame', getRecordType({ type: nf.type }), 'no_frame');
check('noFrameResult() 每次返回新对象（不是共享单例）',
    noFrameResult() !== nf, true);

console.log('\n=== 3. 上传成功分支的判定顺序 ===');

// 无图框必须在 judgeSize 之前拦下：否则 judgeSize(0,0) 会返回 label='无效尺寸'，
// "没图框"就被说成"尺寸算错了"。
const iNoFrame = src.indexOf('if (isNoFrameResult(data))');
const iJudge = src.indexOf('const result = judgeSize(w, h);', src.indexOf("fetch('http://localhost:5000/upload'"));
check('无图框分支出现在 judgeSize(w, h) 之前', iNoFrame > 0 && iJudge > 0 && iNoFrame < iJudge, true);
check('无图框分支不调用 judgeSize（尺寸列留 "—"，不伪造尺寸）',
    src.slice(iNoFrame, src.indexOf('const w = data.width;', iNoFrame)).includes('judgeSize'), false);

console.log('\n=== 4. 图框数筛选：0 框不能落进「单图框 (=1)」 ===');

// recordMatchesFilter 里的 getActiveFilters / getActiveFcFilters / mixedLevel 是外部
// 依赖，这里注入可切换的桩，被测函数本身仍是 app.js 里的真实实现。
let _activeFilters = [];
let _fcFilters = [];
const getActiveFilters = () => _activeFilters;
const getActiveFcFilters = () => _fcFilters;
const mixedLevel = () => '';   // 无候选记录不会有混合级别
const recordMatchesFilter = eval('(' + extractFn('recordMatchesFilter') + ')');

const rec0 = { type: 'no_frame', frameCount: 0, isFailed: false };
const rec1 = { type: 'standard', frameCount: 1, isFailed: false };
const rec3 = { type: 'standard', frameCount: 3, isFailed: false };

_activeFilters = []; _fcFilters = [];
check('未勾任何图框数条件 → 0 框记录正常显示', recordMatchesFilter(rec0), true);

_fcFilters = ['fc1'];
check('勾「单图框 (=1)」→ 0 框记录被排除', recordMatchesFilter(rec0), false);
check('勾「单图框 (=1)」→ 1 框记录命中', recordMatchesFilter(rec1), true);

_fcFilters = ['fcmany'];
check('勾「多图框 (>1)」→ 0 框记录被排除', recordMatchesFilter(rec0), false);
check('勾「多图框 (>1)」→ 3 框记录命中', recordMatchesFilter(rec3), true);

_fcFilters = [];
_activeFilters = ['no_frame'];
check('勾「无图框」类型 → 0 框记录命中', recordMatchesFilter(rec0), true);
_activeFilters = ['standard'];
check('只勾「标准」→ 0 框记录被排除', recordMatchesFilter(rec0), false);

console.log('\n=== 5. 三处结构必须同步（筛选 / 统计 / 样式） ===');

// ① index.html 的筛选复选框值集合 ↔ app.js buildExportSuffix 里的 allTypes
const fcBlock = html.slice(html.indexOf('id="filterCheckboxes"'),
    html.indexOf('</div>', html.indexOf('id="filterCheckboxes"')));
const htmlFilters = [...fcBlock.matchAll(/value="([a-z_]+)"/g)].map(m => m[1]).sort();
const allTypesSrc = extractConst('allTypes');
const jsAllTypes = eval('(' + allTypesSrc.split('=')[1].trim().replace(/;$/, '') + ')').slice().sort();
check('筛选复选框与 allTypes 完全一致（漏同步会让导出文件名从「全部」退化）',
    jsAllTypes, htmlFilters);

check('筛选栏有「无图框」选项', htmlFilters.includes('no_frame'), true);

// ② 类型统计档位
check('TYPE_STATS_META 含 no_frame 档位',
    TYPE_STATS_META.some(m => m.key === 'no_frame' && m.label === '无图框'), true);
check('TYPE_STATS_META 档位数 = 6（含 0 数量的类型也要输出，sheet 结构才稳定）',
    TYPE_STATS_META.length, 6);

// ③ 样式：徽标与图例点
check('style.css 有 .type-tag.noframe', /\.type-tag\.noframe\s*\{/.test(css), true);
check('style.css 有 .legend-item .dot.noframe', /\.legend-item\s+\.dot\.noframe\s*\{/.test(css), true);
check('index.html 图例有「无图框」', html.includes('dot noframe'), true);

console.log('\n=== 6. 解析失败路径不受影响 ===');

check('addFailedRecord 仍把异常记成 failed（≠ 无图框）',
    src.includes("type: 'failed',") && src.includes('❌ 解析失败：'), true);
check('后端抛出的 error 仍走 addFailedRecord（走 errMsg 分支）',
    src.includes('addFailedRecord(file.relativePath || file.name, errMsg, filePath)'), true);

// ---- 7、8 两节要跑真实页面里的异步流程，统一放进 async 包 ----
(async () => {
    console.log('\n=== 7. 真实页面渲染（jsdom 载入 index.html + app.js） ===');

    // 前面几组测的是函数语义，这一组测"真的在页面上长什么样"：把浏览器端脚本整套
    // 载入 jsdom，走 addRecord → renderRecords，直接查那一行的 DOM 文本。
    let dom = null, w = null;
    try {
        const { JSDOM } = require('jsdom');
        dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost:5000/' });
        w = dom.window;
        w.eval(src);
    } catch (e) {
        console.log(`       注意：jsdom 不可用（${e.message}）→ 第 7、8 节未执行，npm install 后重跑`);
        console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
        process.exit(fail === 0 ? 0 : 1);
    }

    w.addRecord(0, 0, w.noFrameResult(), '固定环01--05', 'C:/x/固定环01--05.dwg',
        { frameCount: 0, candidates: [], layoutCounts: null, xrefWarnings: [] });
    const row = w.document.querySelector('#recordBody tr:last-child');
    const text = row ? row.textContent.replace(/\s+/g, ' ').trim() : '';
    const tds = row ? [...row.querySelectorAll('td')].map(td => td.textContent.trim()) : [];

    check('行已渲染出来', !!row, true);
    check('图框数列显示 "0"（不是 "—"，0 是有效结果）', tds[5], '0');
    check('宽/高列显示 "—"（没有尺寸就不编造）', [tds[3], tds[4]], ['—', '—']);
    check('类型徽标用 .type-tag.noframe', !!row.querySelector('.type-tag.noframe'), true);
    check('类型徽标文案 = 无图框',
        row.querySelector('.type-tag.noframe').textContent.trim(), '无图框');
    check('判断结果列写明「未检测到图框」且注明非解析失败',
        /未检测到图框/.test(text) && /非解析失败/.test(text), true);
    check('该行不标"解析失败"徽标', !!row.querySelector('.type-tag.failed'), false);
    check('行不带 record-failed 样式（不是失败行）', row.className.includes('record-failed'), false);

    // 对照：真正的失败记录仍然是"解析失败 + failed + 灰蓝徽标"
    w.addFailedRecord('坏图.dwg', 'max() iterable argument is empty', 'C:/x/坏图.dwg');
    const frow = w.document.querySelector('#recordBody tr:last-child');
    const ftds = [...frow.querySelectorAll('td')].map(td => td.textContent.trim());
    check('真失败记录：图框数仍显示 "—"（无 frameCount）', ftds[5], '—');
    check('真失败记录：徽标是 解析失败', frow.querySelector('.type-tag.failed').textContent.trim(), '解析失败');
    check('真失败记录：带 record-failed 样式', frow.className.includes('record-failed'), true);

    // 类型分布条：无图框要独立成段，不能被并进"非标准"
    w.renderTypeStats();
    const legend = w.document.getElementById('typeStats').textContent;
    check('类型分布条出现「无图框 1 张」', /无图框 1 张/.test(legend), true);
    check('类型分布条里「非标准 0 张」（未被并进去）', /非标准 0 张/.test(legend), true);

    console.log('\n=== 8. 端到端：parseSingleFile 真的走完"响应 → 渲染"链路 ===');

    // 这一节不打桩任何内部函数，只替换 fetch：从后端响应体开始，经过
    // isNoFrameResult → addRecord → renderRecords，直接看落到表格里的那一行。
    // 前面几节证明"函数对"，这一节证明"接起来也对"。
    const NOFRAME_BODY = {
        width: 0, height: 0, unit: 'mm', frame_count: 0,
        frame_counts_by_layout: {}, candidates: [], xref_warnings: [], no_frame: true,
    };
    const mkResp = (body, status) => ({
        ok: status === 200,
        status: status || 200,
        json: async () => body,
    });
    const mkFile = (name) => {
        const f = new w.File(['xx'], name);
        f.relativePath = name;
        return f;
    };

    // 场景 A：后端 smart 直接判定无图框（未被剔光前就空）→ 一次请求，不再重试
    const modesA = [];
    w.fetch = async (url, opts) => {
        modesA.push(opts.body.get('mode'));
        return mkResp(NOFRAME_BODY, 200);
    };
    const rowsBeforeA = w.document.querySelectorAll('#recordBody tr').length;
    const okA = await w.parseSingleFile(mkFile('固定环01--05.dwg'));
    const rowA = w.document.querySelector('#recordBody tr:last-child');
    check('A：返回 true（算解析成功，不是失败）', okA, true);
    check('A：只发了 1 次请求（0 框不该再触发 force_max 重试）', modesA, ['smart']);
    check('A：表格里是「无图框」而不是「解析失败」',
        !!rowA.querySelector('.type-tag.noframe') && !rowA.querySelector('.type-tag.failed'), true);
    check('A：图框数 0', [...rowA.querySelectorAll('td')][5].textContent.trim(), '0');
    check('A：只新增 1 条记录',
        w.document.querySelectorAll('#recordBody tr').length - rowsBeforeA, 1);

    // 场景 B：旧后端行为——smart 抛「未检测到图框」，重试 force_max 后拿到无图框载荷
    const modesB = [];
    w.fetch = async (url, opts) => {
        const m = opts.body.get('mode');
        modesB.push(m);
        if (m === 'smart') return { ok: false, status: 500, json: async () => ({ error: '解析失败: 未检测到图框（图纸可能没有标准图框）' }) };
        return mkResp(NOFRAME_BODY, 200);
    };
    const rowsBeforeB = w.document.querySelectorAll('#recordBody tr').length;
    const okB = await w.parseSingleFile(mkFile('固定环01--05-v2.dwg'));
    const rowsB = [...w.document.querySelectorAll('#recordBody tr')];
    // 关键：重试过程**不能**留下一条"解析失败"的行，最终只有 1 行「无图框」
    check('B：返回 true，且只新增 1 条记录（重试不产生失败行）',
        [okB, rowsB.length - rowsBeforeB], [true, 1]);
    check('B：请求序列 = smart → force_max', modesB, ['smart', 'force_max']);
    check('B：最终行是「无图框」',
        !!rowsB[rowsB.length - 1].querySelector('.type-tag.noframe'), true);

    // 场景 C：后端真空了（500 且不是"未检测到图框"）仍必须是「解析失败」
    //   护栏：不能让 no_frame 的兜底把真异常也吞成"无图框"
    const modesC = [];
    w.fetch = async (url, opts) => {
        modesC.push(opts.body.get('mode'));
        return { ok: false, status: 500, json: async () => ({ error: '解析失败: max() iterable argument is empty' }) };
    };
    const rowsBeforeC = w.document.querySelectorAll('#recordBody tr').length;
    const okC = await w.parseSingleFile(mkFile('坏图.dwg'));
    const rowC = w.document.querySelector('#recordBody tr:last-child');
    check('C：返回 false（真异常仍是失败）', okC, false);
    check('C：不重试（错误信息不含「未检测到图框」）', modesC, ['smart']);
    check('C：徽标是「解析失败」而非「无图框」',
        !!rowC.querySelector('.type-tag.failed') && !rowC.querySelector('.type-tag.noframe'), true);
    check('C：只新增 1 条记录',
        w.document.querySelectorAll('#recordBody tr').length - rowsBeforeC, 1);

    console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
    process.exit(fail === 0 ? 0 : 1);
})();

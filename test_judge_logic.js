// judgeSize 新分类逻辑单元测试：提取 app.js 第 1、2 节（纯函数，无 DOM 依赖）执行
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf-8');
const cut = src.indexOf('function renderResult');
if (cut < 0) { console.error('未找到 renderResult 分割点'); process.exit(1); }
eval(src.slice(0, cut));

let pass = 0, fail = 0;
function check(name, w, h, expectType, expectLabelPart) {
    const r = judgeSize(w, h);
    const typeOk = r.type === expectType;
    const labelOk = !expectLabelPart || (r.label && r.label.includes(expectLabelPart));
    if (typeOk && labelOk) {
        pass++;
        console.log(`PASS  ${name}  ${w}x${h} -> [${r.type}] ${r.label}`);
    } else {
        fail++;
        console.log(`FAIL  ${name}  ${w}x${h} -> [${r.type}] ${r.label}  (期望 ${expectType}${expectLabelPart ? ' / 含 "' + expectLabelPart + '"' : ''})`);
    }
}

// --- 用户预期效果表 ---
check('A0 横版', 1189, 841, 'standard', 'A0');
check('A0 竖版', 841, 1189, 'standard', 'A0');
check('1783x841 标准加长(精确匹配硬编码表)', 1783, 841, 'standard', 'A0+1/2');
check('841x2000 非标加长', 841, 2000, 'extended');
check('84100x59400 A1x100 缩放', 84100, 59400, 'standard', 'A1 × 100');
check('5000x3000 近似匹配兜底', 5000, 3000, 'fallback');
check('无效尺寸', -1, 100, 'nonstandard', '无效尺寸');

// --- 补充边界用例 ---
check('A2 基础幅面', 594, 420, 'standard', 'A2');
check('A2 竖版', 420, 594, 'standard', 'A2');
check('594x1200 非标加长(A1短边)', 594, 1200, 'extended', 'A1');
check('420x1000 非标加长(A2短边)', 420, 1000, 'extended', 'A2');
check('297x900 非标加长(A3短边)', 297, 900, 'extended', 'A3');
check('841x891 特例精确匹配', 841, 891, 'standard', '特例');
check('1683x1189 = A1x2 缩放', 1683, 1189, 'standard', 'A1 × 2');
check('421x298 = A3 尺寸误差≤1%', 421, 298, 'standard', 'A3');
check('A1 横版', 841, 594, 'standard', 'A1');
// 长短比 < 2 且短边匹配 → 不算加长，走兜底
check('594x600 短边匹配但比<2', 594, 600, 'fallback');
// 竖版非标加长（宽<高；横竖版信息在 detail 字段）
check('841x2000 竖版非标加长', 841, 2000, 'extended', 'A0 短边');
// 误差 10mm 内：846≈841 (A0+3/4 短边)，2100≈2080 (A0+3/4 长边)，缩放≈1.008
// 现在走 matchScaledStandard 识别为 A0+3/4 标准（比非标加长更准确）
check('846x2100 近似 A0+3/4', 846, 2100, 'standard', 'A0+3/4');
// 缩放 0.5 倍（比标准小）
check('非整数缩放', 1684, 1190, 'standard', '缩放');
// 极端长宽比：比例偏差 > 40%，落到第⑤级「非标准图纸」（原先会被近似匹配吞掉）
check('3333x777 极端比例', 3333, 777, 'nonstandard', '非标准');
check('5000x1500 细长条', 5000, 1500, 'nonstandard', '非标准');
// 任一边 < 100mm：不可能是图纸幅面，判非标准
check('80x60 尺寸过小', 80, 60, 'nonstandard', '非标准');
// 竖版近似匹配：标签不应带下划线后缀（A0_ -> A0）
check('500x650 竖版近似', 500, 650, 'fallback', 'A0');
// A4 基础幅面
check('A4 竖版', 210, 297, 'standard', 'A4');

// --- 近似匹配专项断言 ---
(function () {
    // 按比例偏差从小到大：0.77%(421x300 经缩放规则) < 17.9%(5000x3000) < 30%(594x600)
    const small = judgeSize(421, 300).confidenceScale;
    const mid = judgeSize(5000, 3000).confidenceScale;
    const large = judgeSize(594, 600).confidenceScale;
    const noUnderscore = !judgeSize(500, 650).label.includes('_');
    const specialNoUnderscore = !judgeSize(841, 891).label.includes('_');
    const monotonic = small >= mid && mid >= large;
    if (noUnderscore && specialNoUnderscore && monotonic) {
        pass++;
        console.log(`PASS  置信度单调性/标签无下划线  conf(0.8%)=${small.toFixed(3)} >= conf(17.9%)=${mid.toFixed(3)} >= conf(30%)=${large.toFixed(3)}`);
    } else {
        fail++;
        console.log(`FAIL  置信度单调性/标签无下划线  noUnderscore=${noUnderscore} special=${specialNoUnderscore} monotonic=${monotonic}`);
    }
})();

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail > 0 ? 1 : 0);

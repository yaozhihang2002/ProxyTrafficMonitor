const assert = require('node:assert/strict');
const {scale,niceMax} = require('./trend-chart.js');
const cases=[[],[null,0,0],[NaN,Infinity,1024],[1,2,500], [2000,10000], [3e6,33e6], [1e11]];
for(const values of cases)for(const log of [false,true]) {
  const s=scale(values,log);
  assert.ok(Number.isFinite(s.maximum)&&s.maximum>0);
  assert.equal(s.fraction(0),0);assert.equal(s.fraction(s.maximum),1);
  const finite=values.filter(Number.isFinite).filter(v=>v>=0);
  for(const value of finite)assert.ok(s.fraction(value)>=0 && s.fraction(value)<=1);
  for(let i=1;i<s.ticks.length;i++)assert.ok(s.ticks[i]>s.ticks[i-1]);
  assert.equal(s.ticks[0],0);assert.equal(s.ticks.at(-1),s.maximum);
  if(log)for(let i=1;i<s.ticks.length;i++)assert.ok(s.fraction(s.ticks[i])-s.fraction(s.ticks[i-1])>=.139);
}
assert.equal(niceMax(33),50);
assert.equal(scale([33*1048576],false).maximum,50*1048576);
assert.ok(scale([33*1048576],true).fraction(1024)>scale([33*1048576],false).fraction(1024));
console.log('Chart scale checks passed: 14 scenarios, zero/missing/large values, monotonicity and log label spacing.');

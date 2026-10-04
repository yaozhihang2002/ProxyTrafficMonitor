/* Local SVG chart. No remote assets or telemetry. CommonJS exports support scale tests. */
(function (root) {
  'use strict';
  const COLORS = {up:'#c96b19',down:'#2475c4'};
  const escape = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function niceMax(value) {
    if (!(value > 0)) return 1024;
    const power = 10 ** Math.floor(Math.log10(value));
    return [1,2,5,10].find(n=>n*power>=value)*power;
  }
  function scale(values, logarithmic) {
    const raw = Math.max(0,...values.filter(Number.isFinite));
    const unit = raw>=1048576?1048576:raw>=1024?1024:1;
    const maximum = raw>0 ? niceMax(raw/unit)*unit : 1024;
    const transform = v => logarithmic ? Math.log1p(v/1024) : v;
    const fraction = v => transform(Math.max(0,v))/transform(maximum);
    let ticks = logarithmic ? [0,...[1024,1048576,1073741824,1099511627776].flatMap(unit=>[1,2,5,10,20,50,100,200,500].map(n=>n*unit)).filter(n=>n<maximum),maximum] : [0,.25,.5,.75,1].map(n=>n*maximum);
    // Keep labels at least 14% apart in screen space, including the top label.
    if (logarithmic) {
      const chosen=[maximum];
      for(let i=ticks.length-2;i>0;i--) if(fraction(chosen[chosen.length-1])-fraction(ticks[i])>=.14 && fraction(ticks[i])>=.14)chosen.push(ticks[i]);
      ticks=[0,...chosen.reverse()];
    }
    return {maximum,ticks,fraction};
  }
  class TrafficTrend {
    constructor(element,formatBytes) {
      this.element=element;this.bytes=formatBytes;this.points=[];this.range=60;this.log=false;this.visible={up:true,down:true};this.selected=null;this.pinned=false;
      element.innerHTML=`<div class="chartTools"><div class="chartLegend"><button type="button" data-series="up" aria-pressed="true"><i style="background:${COLORS.up}"></i>上传</button><button type="button" data-series="down" aria-pressed="true"><i style="background:${COLORS.down}"></i>下载</button></div><div class="chartOptions"><select aria-label="趋势时间范围"><option value="15">近 15 分钟</option><option value="30">近 30 分钟</option><option value="60" selected>近 60 分钟</option></select><button type="button" class="scaleToggle" aria-pressed="false" title="使用对数刻度放大小流量，读数仍为真实字节数">放大小流量</button></div></div><div class="chartCanvas" tabindex="0" role="group" aria-label="每分钟代理流量趋势，左右方向键查看读数，回车固定，Escape 取消固定"><svg viewBox="0 0 800 250" role="img" aria-label="上传与下载，每个点表示一分钟流量"></svg></div><div class="chartBottom"><div class="chartTooltip" role="status" hidden></div><span class="chartReadout">移动鼠标查看读数 · 点击固定</span><button type="button" class="unpin" hidden>取消固定</button></div>`;
      this.canvas=element.querySelector('.chartCanvas');this.svg=element.querySelector('svg');this.tooltip=element.querySelector('.chartTooltip');this.readout=element.querySelector('.chartReadout');this.unpin=element.querySelector('.unpin');
      this.observer=new ResizeObserver(()=>this.draw());this.observer.observe(this.canvas);
      element.querySelector('select').onchange=e=>{this.range=Number(e.target.value);this.selected=null;this.pinned=false;this.draw();this.save()};
      element.querySelector('.scaleToggle').onclick=e=>{this.log=!this.log;e.currentTarget.setAttribute('aria-pressed',this.log);this.draw();this.save()};
      element.querySelectorAll('[data-series]').forEach(b=>b.onclick=()=>{let f=b.dataset.series;if(this.visible[f] && !this.visible[f==='up'?'down':'up'])return;this.visible[f]=!this.visible[f];b.setAttribute('aria-pressed',this.visible[f]);this.draw()});
      this.canvas.onpointermove=e=>{if(!this.pinned)this.selectFromPointer(e)};
      this.canvas.onpointerleave=()=>{if(!this.pinned && document.activeElement!==this.canvas)this.select(null)};
      this.canvas.onclick=e=>{this.selectFromPointer(e);if(this.selected!==null){this.pinned=!this.pinned;this.showSelection()}};
      this.canvas.onfocus=()=>{if(this.selected===null && this.data.length)this.select(this.data.length-1)};
      this.canvas.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End','Enter','Escape'].includes(e.key)){e.preventDefault();let i=this.selected??this.data.length-1;if(e.key==='Escape'){this.pinned=false;this.select(null);return}if(e.key==='Enter'){this.pinned=!this.pinned;this.showSelection();return}this.select(e.key==='Home'?0:e.key==='End'?this.data.length-1:i+(e.key==='ArrowLeft'?-1:1))}};
      this.unpin.onclick=()=>{this.pinned=false;this.select(null)};
      try{const saved=JSON.parse(localStorage.getItem('proxy-monitor-chart')||'{}');if([15,30,60].includes(saved.range))this.range=saved.range;this.log=saved.log===true}catch{}
      element.querySelector('select').value=this.range;element.querySelector('.scaleToggle').setAttribute('aria-pressed',this.log);
    }
    save(){try{localStorage.setItem('proxy-monitor-chart',JSON.stringify({range:this.range,log:this.log}))}catch{}}
    update(points){const bucket=this.selected===null?null:this.data?.[this.selected]?.bucket;this.points=points||[];this.draw(bucket)}
    draw(bucket){
      this.data=this.points.slice(-this.range);const n=this.data.length;this.width=Math.max(300,this.canvas.clientWidth);const height=210;this.svg.setAttribute('viewBox',`0 0 ${this.width} ${height}`);this.plot={left:76,right:this.width-18,top:30,bottom:170};const p=this.plot;
      const series=Object.keys(this.visible).filter(f=>this.visible[f]);this.axis=scale(this.data.flatMap(d=>series.map(f=>d[f])),this.log);
      this.x=i=>p.left+i*(p.right-p.left)/Math.max(1,n-1);this.y=v=>p.bottom-this.axis.fraction(v)*(p.bottom-p.top);
      const unit=this.axis.maximum>=1048576?1048576:this.axis.maximum>=1024?1024:1,unitName=unit===1048576?'MiB':unit===1024?'KiB':'B';
      let html=`<text x="${p.left}" y="14" class="axisLabel">${this.log?'流量 / 分钟 · 对数刻度':unitName+' / 分钟'}</text>`;
      for(const tick of this.axis.ticks){const y=this.y(tick),label=this.log?this.bytes(tick):Number((tick/unit).toPrecision(3));html+=`<line x1="${p.left}" x2="${p.right}" y1="${y}" y2="${y}" class="chartGrid"/><text x="${p.left-12}" y="${y+4}" text-anchor="end" class="axisLabel">${escape(label)}</text>`}
      for(let j=0;j<5 && n;j++){const i=Math.round(j*(n-1)/4),x=this.x(i);html+=`<text x="${x}" y="197" text-anchor="${j===0?'start':j===4?'end':'middle'}" class="axisLabel">${escape(this.time(this.data[i].bucket))}</text>`}
      for(const field of series){let run=[];const flush=()=>{if(run.length>1)html+=`<polyline points="${run.join(' ')}" fill="none" stroke="${COLORS[field]}" stroke-width="2.2" ${field==='up'?'stroke-dasharray="6 4"':''} stroke-linejoin="round"/>`;run=[]};
        this.data.forEach((d,i)=>{if(d[field]===null || !Number.isFinite(d[field])){flush();return}const x=this.x(i),y=this.y(d[field]);run.push(`${x},${y}`);html+=field==='up'?`<rect x="${x-2.5}" y="${y-2.5}" width="5" height="5" fill="${COLORS[field]}"/>`:`<circle cx="${x}" cy="${y}" r="2.5" fill="${COLORS[field]}"/>`});flush()}
      if(!this.data.some(d=>d.up!==null && Number.isFinite(d.up)))html+=`<text x="${(p.left+p.right)/2}" y="110" text-anchor="middle" class="axisLabel">此时间范围暂无有效采样</text>`;
      html+='<g class="selectionLayer"></g>';this.svg.innerHTML=html;
      if(bucket!==undefined && bucket!==null){const i=this.data.findIndex(d=>d.bucket===bucket);this.selected=i<0?null:i;if(i<0)this.pinned=false}
      this.showSelection();
      this.element.querySelectorAll('[data-series]').forEach(b=>{b.disabled=this.visible[b.dataset.series] && series.length===1});
    }
    time(ts){return new Date(ts*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}
    selectFromPointer(e){if(!this.data.length)return;const r=this.svg.getBoundingClientRect(),x=(e.clientX-r.left)*this.width/r.width;this.select(Math.round((x-this.plot.left)/(this.plot.right-this.plot.left)*(this.data.length-1)))}
    select(index){this.selected=index===null?null:Math.max(0,Math.min(this.data.length-1,index));this.showSelection()}
    showSelection(){
      const layer=this.svg.querySelector('.selectionLayer');if(!layer)return;layer.innerHTML='';this.unpin.hidden=!this.pinned;
      if(this.selected===null || !this.data[this.selected]){this.tooltip.hidden=true;this.readout.hidden=false;this.readout.textContent='移动鼠标查看读数 · 点击固定 · 方向键也可查看';return}
      const d=this.data[this.selected],x=this.x(this.selected),missing=d.up===null || d.down===null;
      layer.innerHTML=`<line x1="${x}" x2="${x}" y1="${this.plot.top}" y2="${this.plot.bottom}" class="crosshair"/>`+['up','down'].filter(f=>this.visible[f] && Number.isFinite(d[f])).map(f=>`<circle cx="${x}" cy="${this.y(d[f])}" r="4.5" fill="white" stroke="${COLORS[f]}" stroke-width="2"/>`).join('');
      const latest=d.bucket===this.points[this.points.length-1]?.bucket;
      this.tooltip.innerHTML=`<strong>${this.pinned?'已固定 · ':''}${escape(this.time(d.bucket))} — ${escape(this.time(d.bucket+60))}</strong><span class="tooltipState">${missing?'无有效采样':latest?'当前分钟 · 仍在累计':'每分钟流量'}</span>${missing?'':`<span><i style="background:${COLORS.up}"></i>上传 <b>${escape(this.bytes(d.up))}</b></span><span><i style="background:${COLORS.down}"></i>下载 <b>${escape(this.bytes(d.down))}</b></span><span class="tooltipTotal">合计 <b>${escape(this.bytes(d.up+d.down))}</b></span>`}`;
      this.tooltip.hidden=false;this.readout.hidden=true;
      this.readout.textContent=(this.pinned?'已固定 · ':'')+this.time(d.bucket)+(missing?' · 无有效采样':' · 上传 '+this.bytes(d.up)+' / 下载 '+this.bytes(d.down));
    }
  }
  if(typeof module!=='undefined')module.exports={niceMax,scale};else root.TrafficTrend=TrafficTrend;
})(typeof window!=='undefined'?window:globalThis);

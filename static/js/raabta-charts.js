/* Raabta 360 — dependency-free SVG charts (donut, funnel, line, bars, ring, dot-map) */
(function(w){
  var NS='http://www.w3.org/2000/svg', uid=0;
  function $(el){return typeof el==='string'?document.getElementById(el):el}
  function mk(tag,attrs,parent){var n=document.createElementNS(NS,tag);for(var k in attrs)n.setAttribute(k,attrs[k]);if(parent)parent.appendChild(n);return n}
  function fmt(n){n=+n||0;if(n>=1e6)return (n/1e6).toFixed(n%1e6?1:0)+'M';if(n>=1e3)return (n/1e3).toFixed(n%1e3?1:0)+'K';return ''+Math.round(n)}
  function money(n){return (w.RB_CUR||'$')+Math.round(+n||0).toLocaleString('en-US')}
  function grad(svg,id,c1,c2,vert){var d=svg.querySelector('defs')||mk('defs',{},svg);var g=mk('linearGradient',{id:id,x1:0,y1:0,x2:vert?0:1,y2:vert?1:0},d);mk('stop',{offset:'0%','stop-color':c1},g);mk('stop',{offset:'100%','stop-color':c2},g)}
  function smooth(p,base){if(p.length<2)return '';var d='M'+p[0][0]+' '+p[0][1];for(var i=0;i<p.length-1;i++){var p0=p[i-1]||p[i],p1=p[i],p2=p[i+1],p3=p[i+2]||p2;var c1x=p1[0]+(p2[0]-p0[0])/6,c1y=p1[1]+(p2[1]-p0[1])/6,c2x=p2[0]-(p3[0]-p1[0])/6,c2y=p2[1]-(p3[1]-p1[1])/6;if(base!=null){c1y=Math.min(c1y,base);c2y=Math.min(c2y,base)}d+=' C'+c1x+' '+c1y+','+c2x+' '+c2y+','+p2[0]+' '+p2[1]}return d}
  function niceMax(v){if(v<=0)return 1;var p=Math.pow(10,Math.floor(Math.log10(v))),f=v/p;return (f<=1?1:f<=2?2:f<=2.5?2.5:f<=5?5:10)*p}
  function legend(el,items,total,showVal){el=$(el);el.innerHTML=items.map(function(d){var pct=total?Math.round(d.value/total*100):0;return '<div><i style="background:'+d.color+'"></i><span>'+d.label+'</span><em>'+(showVal===false?'':pct+'%'+(d.value!=null?' ('+d.value+')':''))+'</em></div>'}).join('')}

  /* ---------- Donut ---------- */
  function donut(el,items,o){el=$(el);o=o||{};var S=170,R=62,SW=22,C=2*Math.PI*R,tot=items.reduce(function(a,d){return a+d.value},0);
    var svg=mk('svg',{viewBox:'0 0 '+S+' '+S,width:o.size||150,height:o.size||150,class:'ch-donut-svg'});
    mk('circle',{cx:S/2,cy:S/2,r:R,fill:'none',stroke:'#eef2fa','stroke-width':SW},svg);
    var off=0,gap=tot?2.5:0;
    if(tot)items.forEach(function(d){if(d.value<=0)return;var len=d.value/tot*C;mk('circle',{cx:S/2,cy:S/2,r:R,fill:'none',stroke:d.color,'stroke-width':SW,'stroke-dasharray':Math.max(len-gap,.5)+' '+(C-Math.max(len-gap,.5)),'stroke-dashoffset':-off,transform:'rotate(-90 '+S/2+' '+S/2+')','stroke-linecap':'butt'},svg).appendChild(Object.assign(mk('title',{}),{textContent:d.label+': '+d.value}));off+=len});
    var t=mk('text',{x:S/2,y:S/2+2,'text-anchor':'middle','font-family':'Poppins,sans-serif','font-weight':700,'font-size':22,fill:'#0f1b3d'},svg);t.textContent=(o.center&&o.center.value!=null)?o.center.value:tot.toLocaleString();
    var t2=mk('text',{x:S/2,y:S/2+20,'text-anchor':'middle','font-size':9.5,fill:'#7a869f'},svg);t2.textContent=(o.center&&o.center.label)||'Total';
    el.innerHTML='';el.appendChild(svg);return tot}

  /* ---------- Ring gauge (win rate) ---------- */
  function ring(el,pct,color){el=$(el);var S=64,R=26,C=2*Math.PI*R;var svg=mk('svg',{viewBox:'0 0 64 64',width:64,height:64});
    var id='rg'+(++uid);grad(svg,id,'#12b8e6','#1fd3a3');
    mk('circle',{cx:32,cy:32,r:R,fill:'none',stroke:'#e6f6fb','stroke-width':7},svg);
    mk('circle',{cx:32,cy:32,r:R,fill:'none',stroke:'url(#'+id+')','stroke-width':7,'stroke-linecap':'round','stroke-dasharray':(C*Math.min(pct,100)/100)+' '+C,transform:'rotate(-90 32 32)'},svg);
    el.innerHTML='';el.appendChild(svg)}

  /* ---------- Funnel (stacked 3D layers) ---------- */
  function funnel(el,stages,o){el=$(el);o=o||{};var W=190,H=230,n=stages.length,gap=5,lh=(H-gap*(n-1))/n,top=W,bot=W*.22;
    var svg=mk('svg',{viewBox:'0 0 '+W+' '+(H+8),class:'ch-svg'});
    stages.forEach(function(s,i){var y=i*(lh+gap),wt=top-(top-bot)*(i/n),wb=top-(top-bot)*((i+1)/n),id='fg'+(++uid);
      var c=s.color||'#3b8bff';grad(svg,id,c,s.color2||c,true);
      var x1=(W-wt)/2,x2=(W+wt)/2,x3=(W+wb)/2,x4=(W-wb)/2,r=7;
      mk('path',{d:'M'+x1+' '+y+' L'+x2+' '+y+' L'+x3+' '+(y+lh)+' L'+x4+' '+(y+lh)+' Z',fill:'url(#'+id+')',opacity:.96},svg);
      mk('ellipse',{cx:W/2,cy:y+1,rx:wt/2,ry:5,fill:'#fff',opacity:.28},svg);
      mk('path',{d:'M'+x4+' '+(y+lh)+' L'+x3+' '+(y+lh)+' L'+x3+' '+(y+lh+3)+' L'+x4+' '+(y+lh+3)+'Z',fill:'rgba(0,0,0,.14)'},svg)});
    var rows=document.createElement('div');rows.className='funnel-rows';var first=stages[0].value||1;
    rows.innerHTML=stages.map(function(s,i){var p=Math.round((s.value/first)*1000)/10;return '<div><i style="background:'+s.color+'"></i><b>'+s.label+'</b><span>'+(o.money?((w.RB_CUR||'$')+fmt(s.value)):s.value.toLocaleString())+'</span><em>'+(o.showPct===false?'':p+'%')+'</em></div>'}).join('');
    el.innerHTML='';el.className=(el.className||'')+' funnel-wrap';
    var wrap=document.createElement('div');wrap.style.flex='0 0 '+(o.svgPct||42)+'%';wrap.style.maxWidth='200px';wrap.style.minWidth='90px';wrap.appendChild(svg);el.appendChild(wrap);el.appendChild(rows)}

  /* ---------- Line / area chart with tooltip ---------- */
  function line(el,cfg){el=$(el);var W=Math.max(cfg.width||el.clientWidth||640,260),H=cfg.height||250,m={l:44,r:12,t:14,b:28},iw=W-m.l-m.r,ih=H-m.t-m.b;
    el.innerHTML='';el.classList.add('ch-wrap');
    var svg=mk('svg',{viewBox:'0 0 '+W+' '+H,class:'ch-svg'});el.appendChild(svg);
    var all=[];cfg.series.forEach(function(s){all=all.concat(s.data)});var max=cfg.max||niceMax(Math.max.apply(null,all.concat([1])));
    var ticks=cfg.ticks||5,fm=cfg.yfmt||fmt;
    for(var i=0;i<=ticks;i++){var v=max/ticks*i,y=m.t+ih-ih*(i/ticks);mk('line',{x1:m.l,x2:W-m.r,y1:y,y2:y,stroke:'#eef2f9','stroke-dasharray':i?'3 4':'0'},svg);var t=mk('text',{x:m.l-8,y:y+3.5,'text-anchor':'end','font-size':9.5,fill:'#8b97b3'},svg);t.textContent=fm(v)}
    var n=cfg.labels.length,step=n>1?iw/(n-1):0,skip=Math.ceil(n/(cfg.maxLabels||9));
    cfg.labels.forEach(function(l,i){if(i%skip)return;var t=mk('text',{x:m.l+step*i,y:H-8,'text-anchor':'middle','font-size':9.5,fill:'#8b97b3'},svg);t.textContent=l});
    var pts=[];
    cfg.series.forEach(function(s,si){var id='lg'+(++uid),p=s.data.map(function(v,i){return [m.l+step*i,m.t+ih-ih*(v/max)]});pts.push(p);
      if(s.area){grad(svg,id,s.color,'#ffffff',true);var a=mk('path',{d:smooth(p,m.t+ih)+' L'+p[p.length-1][0]+' '+(m.t+ih)+' L'+p[0][0]+' '+(m.t+ih)+' Z',fill:'url(#'+id+')',opacity:.22},svg)}
      mk('path',{d:smooth(p,m.t+ih),fill:'none',stroke:s.color,'stroke-width':2.2,'stroke-linecap':'round'},svg);
      if(s.dots!==false)p.forEach(function(q){mk('circle',{cx:q[0],cy:q[1],r:2.8,fill:'#fff',stroke:s.color,'stroke-width':1.6},svg)})});
    var tip=document.createElement('div');tip.className='ch-tip';el.appendChild(tip);
    var guide=mk('line',{y1:m.t,y2:m.t+ih,stroke:'#9ec0ff','stroke-dasharray':'3 3',opacity:0},svg);
    var hit=mk('rect',{x:m.l,y:m.t,width:iw,height:ih,fill:'transparent'},svg);
    hit.addEventListener('mousemove',function(e){var r=svg.getBoundingClientRect(),sx=(e.clientX-r.left)*W/r.width,i=Math.max(0,Math.min(n-1,Math.round((sx-m.l)/(step||1))));
      guide.setAttribute('x1',pts[0][i][0]);guide.setAttribute('x2',pts[0][i][0]);guide.setAttribute('opacity',1);
      var s=cfg.series[cfg.tipSeries||0],html=cfg.series.length>1?cfg.series.map(function(s){return s.name+': '+(cfg.tipfmt||fm)(s.data[i])}).join('<br>'):(cfg.tipfmt||money)(s.data[i]);
      tip.innerHTML=html+'<small>'+cfg.labels[i]+'</small>';tip.style.left=(pts[0][i][0]/W*r.width)+'px';tip.style.top=(Math.min.apply(null,pts.map(function(p){return p[i][1]}))/H*r.height)+'px';tip.style.opacity=1});
    hit.addEventListener('mouseleave',function(){tip.style.opacity=0;guide.setAttribute('opacity',0)});
    return svg}

  /* ---------- Bar chart ---------- */
  function bars(el,cfg){el=$(el);var W=Math.max(cfg.width||el.clientWidth||640,260),H=cfg.height||240,m={l:40,r:8,t:14,b:26},iw=W-m.l-m.r,ih=H-m.t-m.b;el.innerHTML='';
    var svg=mk('svg',{viewBox:'0 0 '+W+' '+H,class:'ch-svg'});el.appendChild(svg);
    var all=cfg.values.concat(cfg.target||[]);var max=niceMax(Math.max.apply(null,all.concat([1])));
    for(var i=0;i<=4;i++){var y=m.t+ih-ih*i/4;mk('line',{x1:m.l,x2:W-m.r,y1:y,y2:y,stroke:'#eef2f9','stroke-dasharray':i?'3 4':'0'},svg);var t=mk('text',{x:m.l-8,y:y+3.5,'text-anchor':'end','font-size':9.5,fill:'#8b97b3'},svg);t.textContent=fmt(max/4*i)}
    var n=cfg.labels.length,bw=iw/n,w=Math.min(bw*.46,26);grad(svg,'bg'+uid,'#46a0ff','#1f5bff',true);var gid='bg'+uid;uid++;
    cfg.labels.forEach(function(l,i){var cx=m.l+bw*i+bw/2,v=cfg.values[i],h=ih*v/max;
      if(cfg.target){var th=ih*cfg.target[i]/max;mk('rect',{x:cx-w/2-3,y:m.t+ih-th,width:w+6,height:th,rx:5,fill:'#e4edff'},svg)}
      var r=mk('rect',{x:cx-w/2,y:m.t+ih-h,width:w,height:Math.max(h,1.5),rx:5,fill:'url(#'+gid+')'},svg);r.appendChild(Object.assign(mk('title',{}),{textContent:l+': '+(cfg.fmt||money)(v)}));
      var t=mk('text',{x:cx,y:H-8,'text-anchor':'middle','font-size':9.5,fill:'#8b97b3'},svg);t.textContent=l})}

  /* ---------- Dotted world map ---------- */
  function map(el,url,regions){el=$(el);fetch(url).then(function(r){return r.json()}).then(function(M){
    var cell=7,svg=mk('svg',{viewBox:'0 0 '+(M.w*cell)+' '+(M.h*cell),class:'ch-svg'});
    Object.keys(M.rows).forEach(function(y){M.rows[y].forEach(function(x){mk('circle',{cx:x*cell+cell/2,cy:y*cell+cell/2,r:2.1,fill:'#cfe0fb'},svg)})});
    var colors=['#1f6bff','#7a5af8','#12b76a','#f79009','#06aed4'];
    regions.forEach(function(r,i){var c=M.c[(r.name||'').toLowerCase()];if(!c)return;var cx=c[0]*cell+cell/2,cy=c[1]*cell+cell/2,col=colors[i%colors.length];
      for(var dx=-3;dx<=3;dx++)for(var dy=-3;dy<=3;dy++){if(dx*dx+dy*dy<=9){mk('circle',{cx:cx+dx*cell,cy:cy+dy*cell,r:2.9,fill:col,opacity:.8},svg)}}
      mk('circle',{cx:cx,cy:cy,r:26,fill:col,opacity:.12},svg)});
    el.innerHTML='';el.appendChild(svg)}).catch(function(){})}

  w.RB={donut:donut,ring:ring,funnel:funnel,line:line,bars:bars,map:map,legend:legend,money:money,fmt:fmt};
})(window);

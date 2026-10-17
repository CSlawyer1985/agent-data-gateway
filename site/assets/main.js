(()=>{
const RM=matchMedia('(prefers-reduced-motion: reduce)').matches;
const FILES=[['扫描合同.pdf','#ff3b4e'],['公章·骑缝章.jpg','#ffb020'],['手写批注.png','#ffb020'],['财务账.xlsx','#38f2ff'],['厂区图纸.dwg','#38f2ff'],['往来邮件.msg','#ffb020'],['证据包.7z','#ff3b4e'],['监控录像.mp4','#ff3b4e'],['聊天记录','#38f2ff'],['判决书.doc','#ffb020'],['修订稿.docx','#38f2ff'],['庭审录音.m4a','#ffb020'],['邮箱归档.pst','#ff3b4e'],['论文.caj','#ff3b4e'],['网页存证.html','#38f2ff'],['演示.pptx','#ffb020'],['图纸.dxf','#38f2ff'],['旧表.xls','#38f2ff'],['电子书.epub','#ffb020'],['照片.heic','#ffb020']];
// pain chips
const pc=document.getElementById('painChips');FILES.forEach((f,i)=>{const s=document.createElement('span');s.className='chip';s.textContent=f[0];s.style.color=f[1];s.style.borderColor=f[1];s.style.setProperty('--r',((i*37%11)-5)+'deg');s.style.setProperty('--d',(-i*.3)+'s');pc.appendChild(s)});
// hero canvas: chaos -> order on scroll
const cv=document.getElementById('heroCanvas'),x=cv.getContext('2d');let W,H,dpr,P=[];
function size(){dpr=Math.min(2,devicePixelRatio||1);W=cv.clientWidth;H=cv.clientHeight;cv.width=W*dpr;cv.height=H*dpr;x.setTransform(dpr,0,0,dpr,0,0);
 const n=W<700?24:48;P=[];const cols=W<700?3:6,cw=W<700?110:150,ch=34;const gx=W<700?(W-cols*cw)/2:W*.52,gy=H*.18;
 for(let i=0;i<n;i++){const f=FILES[i%FILES.length];P.push({f,sx:Math.random()*W,sy:Math.random()*H,r:(Math.random()-.5)*1.2,ph:Math.random()*6,tx:gx+(i%cols)*cw,ty:gy+Math.floor(i/cols)*(ch+8)})}}
size();addEventListener('resize',size);
const hero=document.querySelector('.hero');
function prog(){const r=hero.getBoundingClientRect();return Math.min(1,Math.max(0,-r.top/(r.height-innerHeight)))}
const ease=t=>t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;
function draw(now){const t=now/1000,p=RM?1:ease(prog());x.clearRect(0,0,W,H);
 x.globalAlpha=.06;x.strokeStyle='#38f2ff';for(let gx=0;gx<W;gx+=48){x.beginPath();x.moveTo(gx,0);x.lineTo(gx,H);x.stroke()}for(let gy=0;gy<H;gy+=48){x.beginPath();x.moveTo(0,gy);x.lineTo(W,gy);x.stroke()}x.globalAlpha=1;
 const small=W<700;
 P.forEach((o,i)=>{const jx=RM?0:Math.sin(t+o.ph)*14*(1-p),jy=RM?0:Math.cos(t*.8+o.ph)*10*(1-p);
  const px=o.sx+(o.tx-o.sx)*p+jx,py=o.sy+(o.ty-o.sy)*p+jy;const w=small?100:138,h=30;
  x.save();x.translate(px,py);x.rotate(o.r*(1-p));x.globalAlpha=(small?.35:.55)+.35*p;
  x.fillStyle='rgba(8,14,20,.9)';x.strokeStyle=p>.95?'#4dff9a':o.f[1];x.lineWidth=1.3;x.beginPath();x.roundRect(-w/2,-h/2,w,h,7);x.fill();x.stroke();
  x.fillStyle=p>.95?'#4dff9a':o.f[1];x.fillRect(-w/2+6,-h/2+7,3,h-14);
  x.fillStyle='#e9eef2';x.font=`600 ${small?11:12}px "PingFang SC","Noto Sans CJK SC",sans-serif`;x.textBaseline='middle';x.fillText(o.f[0],-w/2+14,0);
  if(p>.95){x.fillStyle='#4dff9a';x.fillText('✓',w/2-16,0)}x.restore()});
 if(p>.6&&!small){x.globalAlpha=(p-.6)/.4;x.fillStyle='#4dff9a';x.font='600 13px Menlo,monospace';x.fillText('all_items_accounted = true  ·  coverage 97.6%  ·  unresolved: 1 (CAJ)',W*.52,H*.18-34);x.globalAlpha=1}
 if(!RM)requestAnimationFrame(draw)}
requestAnimationFrame(draw);if(RM)addEventListener('scroll',()=>requestAnimationFrame(draw));
// raw garble
const raw='证据包.7z  [binary 0x377ABCAF271C...] ?????\n扫描合同.pdf  (no text layer) → ""\n财务账.xlsx  Sheet1 only… 合并单元格 → NaN NaN NaN\n往来邮件.msg  ��Ole2��__substg1.0_0037001F��附件: ???\n监控录像.mp4  [skipped: unsupported]\n修订稿.docx  批注丢失 · 修订混入正文\n论文.caj  [silently dropped]\n\n> 模型：「已阅读全部材料。根据合同，保证期间……」\n> 来源？—— 无法定位原文。';
const rt=document.getElementById('rawTxt');rt.textContent=raw;
let gl=0;setInterval(()=>{if(RM)return;gl++;const ch='▯�#%?$';rt.textContent=raw.split('').map((c,i)=>c!='\n'&&Math.random()<.025?ch[(i+gl)%ch.length]:c).join('')},140);
// compare slider
const cmp=document.getElementById('compare'),pane=document.getElementById('adgPane'),hd=document.getElementById('gateHandle');
function setG(v){v=Math.max(3,Math.min(97,v));pane.style.clipPath=`inset(0 0 0 ${v}%)`;hd.style.left=v+'%';hd.setAttribute('aria-valuenow',Math.round(v))}
let drag=false;const mv=e=>{if(!drag)return;const r=cmp.getBoundingClientRect();const cx=(e.touches?e.touches[0].clientX:e.clientX);setG((cx-r.left)/r.width*100)};
cmp.addEventListener('pointerdown',e=>{drag=true;mv(e)});addEventListener('pointermove',mv);addEventListener('pointerup',()=>drag=false);
hd.addEventListener('keydown',e=>{const v=parseFloat(hd.style.left)||50;if(e.key=='ArrowLeft')setG(v-5);if(e.key=='ArrowRight')setG(v+5)});
// auto sweep once in view
new IntersectionObserver((es,o)=>{es.forEach(en=>{if(en.isIntersecting){o.disconnect();if(RM)return;let s=performance.now();const an=n=>{const k=(n-s)/2200;if(k>1||drag)return;setG(50+Math.sin(k*Math.PI*2)*-38);requestAnimationFrame(an)};requestAnimationFrame(an)}})},{threshold:.5}).observe(cmp);
// pipeline
const STG=['发现','安全展开','类型识别','特征路由','调度','质量定稿','锚点派生','谱系记录'];
const ROUTES=[
 ['扫描 PDF','pdf_class = scanned（无文字层）；quality 下并跑文档 VLM 视角',['mineru','pdf_render'],'页码 · bbox 坐标'],
 ['数字 PDF','有文字层的数字 PDF；加密 PDF 先试空用户口令',['docling','pdfplumber','pypdf','pdf_lite'],'页码 · bbox 坐标'],
 ['Word 批注修订','OOXML Word，保留段落结构与批注修订',['docx_lite','anydoc','officecli','docling'],'/body/p[N] 段落锚点'],
 ['Excel 财务表','.xlsx 保留工作表与单元格；旧 .xls 走专用解析',['xlsx_lite / xls','anydoc','docling'],'工作表!单元格'],
 ['CAD 图纸','.dwg 直读；.dxf 结构化解析',['dwgread','libredwg','dwg_render'],'图层 · 句柄'],
 ['邮件 .msg / .pst','MSG 是 OLE Outlook 容器；PST/OST 由 libpff 展开后回灌',['extract_msg','libpff'],'邮件头字段 · 附件谱系'],
 ['压缩包','发现阶段安全审查后展开，成员重新识别和路由；RAR 显性 unresolved',['stdlib:zipfile / tarfile','py7zr (7z)'],'包内相对路径 · derived_from'],
 ['音视频证据','音频先转录；视频先抽帧并尝试语音转录，最后保留媒体元数据',['video_frames','whisper','av_lite'],'时间码'],
 ['扫描图片 / 印章照片','图片 OCR；quality 档并跑文档 VLM',['paddleocr','rapidocr','image_lite'],'坐标'],
 ['未知格式','类型识别不足时不猜测 parser',['→ unresolved 显性报告'],'不猜测工具、不假装成功']];
const ft=document.getElementById('ftypes'),sg=document.getElementById('stages');
STG.forEach(s=>{const d=document.createElement('div');d.className='stage';d.textContent=s;sg.appendChild(d)});
const pk=document.createElement('div');pk.className='packet';sg.appendChild(pk);
let timer=[];
function play(i){timer.forEach(clearTimeout);timer=[];[...ft.children].forEach((b,j)=>b.classList.toggle('on',i==j));const r=ROUTES[i];
 document.getElementById('rCond').textContent=r[1];document.getElementById('rAnchor').textContent='⌖ '+r[3];
 const rc=document.getElementById('rChain');rc.innerHTML=r[2].map(s=>`<span>${s}</span>`).join(' → ');
 const st=[...sg.querySelectorAll('.stage')];st.forEach(s=>s.classList.remove('lit'));
 const step=RM?0:260;st.forEach((s,j)=>timer.push(setTimeout(()=>{s.classList.add('lit');pk.style.left=(s.offsetLeft+s.offsetWidth/2-8)+'px';pk.style.top=(s.offsetTop+s.offsetHeight/2)+'px'},j*step)));
 [...rc.querySelectorAll('span')].forEach((s,j)=>timer.push(setTimeout(()=>s.classList.add('in'),4*step+j*(RM?0:220))))}
ROUTES.forEach((r,i)=>{const b=document.createElement('button');b.textContent=r[0];b.onclick=()=>play(i);ft.appendChild(b)});
let played=false;new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting&&!played){played=true;play(0)}})},{threshold:.4}).observe(sg);
// reveal & counters
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}}),{threshold:.15});
document.querySelectorAll('.reveal').forEach((el,i)=>{el.style.transitionDelay=(i%4)*80+'ms';io.observe(el)});
new IntersectionObserver((es,o)=>es.forEach(e=>{if(!e.isIntersecting)return;o.disconnect();e.target.querySelectorAll('b[data-n]').forEach(b=>{const n=+b.dataset.n,d=+(b.dataset.d||0),s=b.dataset.s||'';const t0=performance.now();const f=now=>{const k=RM?1:Math.min(1,(now-t0)/1600);b.textContent=(n*(1-Math.pow(1-k,3))).toFixed(d)+s;if(k<1)requestAnimationFrame(f)};requestAnimationFrame(f)})}),{threshold:.4}).observe(document.getElementById('stats'));
// Copy the visible installation instruction as one complete prompt for a local Agent.
const copyButton=document.getElementById('copyAll'),copyStatus=document.getElementById('copyStatus');
copyButton.onclick=async()=>{
 try{
  await navigator.clipboard.writeText(document.getElementById('installPrompt').textContent.trim());
  copyButton.textContent='已复制 ✓';
  copyStatus.textContent='已复制完整指令，请粘贴给你的本机 Agent。';
  setTimeout(()=>copyButton.textContent='复制给 Agent',1500);
 }catch{
  copyStatus.textContent='浏览器未允许复制，请手动选中上方整段指令，再粘贴给 Agent。';
 }
};
// nav highlight
const links=[...document.querySelectorAll('.fnav a[href^="#"]')];
new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)links.forEach(a=>a.classList.toggle('on',a.getAttribute('href')=='#'+e.target.id&&a.className!='brand'))}),{rootMargin:'-45% 0px -50% 0px'}).observe&&document.querySelectorAll('section[id]').forEach(s=>new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)links.forEach(a=>a.classList.toggle('on',a.getAttribute('href')=='#'+s.id))}),{rootMargin:'-45% 0px -50% 0px'}).observe(s));
})();

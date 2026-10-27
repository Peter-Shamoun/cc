const {chromium}=require('playwright');const fs=require('fs');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:12000,height:7000}});
const html=fs.readFileSync(process.argv[2],'utf8');
await p.goto('file://'+process.argv[2]);
// neutralise canvas transform so every board renders at 1:1 in world coords
await p.evaluate(()=>{const w=document.getElementById('world');w.style.transition='none';w.style.transform='none';document.body.style.overflow='visible';document.getElementById('stage').style.overflow='visible';});
await p.evaluate(()=>document.fonts.ready);await p.waitForTimeout(2000);
const out=await p.evaluate(()=>{
 const CAN=CANVAS;
 const boards=[];
 const props=['fill','stroke','stroke-width','stroke-dasharray','opacity','fill-opacity','stroke-opacity','marker-start','marker-end','font-family','font-size','font-weight','font-style','letter-spacing','text-anchor','white-space','stroke-linecap','stroke-linejoin'];
 const cs=el=>{const c=getComputedStyle(el);const o={};for(const k of props)o[k]=c.getPropertyValue(k);return o;};
 for(const bd of document.querySelectorAll('.board')){
  const root=bd.querySelector('svg');const rr=root.getBoundingClientRect();
  const items=[];
  const toRoot=el=>{const m=root.getScreenCTM().inverse().multiply(el.getScreenCTM());return [m.a,m.b,m.c,m.d,m.e,m.f];};
  const walk=el=>{for(const c of el.children){const t=c.tagName.toLowerCase();
   if(['defs','title','desc','style','marker'].includes(t))continue;
   if(t==='svg'||(t==='g'&&c.hasAttribute('transform'))){
     let x,y,w,h;
     if(t==='svg'){x=+c.getAttribute('x');y=+c.getAttribute('y');w=+c.getAttribute('width');h=+c.getAttribute('height');}
     else{const r=c.getBoundingClientRect();const sw=1.6*1.67;x=r.left-rr.left-sw;y=r.top-rr.top-sw;w=r.width+2*sw;h=r.height+2*sw;}
     items.push({t:'icon',x,y,w,h,svg:c.outerHTML,style:cs(c.parentElement)});continue;}
   if(t==='g'){walk(c);continue;}
   const it={t,m:toRoot(c),s:cs(c),a:{}};
   for(const a of c.attributes)it.a[a.name]=a.value;
   if(t==='text'){const bb=c.getBBox();it.bb=[bb.x,bb.y,bb.width,bb.height];
     it.spans=[];const nodes=c.childNodes;
     for(const n of nodes){if(n.nodeType===3)it.spans.push({text:n.textContent,s:it.s});else it.spans.push({text:n.textContent,s:cs(n),a:Object.fromEntries([...n.attributes].map(a=>[a.name,a.value]))});}
   }
   items.push(it);}};
  walk(root);
  const st=bd.style;
  boards.push({slug:bd.dataset.slug,left:parseFloat(st.left),top:parseFloat(st.top),w:parseFloat(st.width),h:parseFloat(st.height),items});
 }
 return {canvas:CAN,boards};});
fs.writeFileSync(process.argv[3],JSON.stringify(out));await b.close();})();

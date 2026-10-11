// DOM binding tests without external packages; visual browser QA is separate.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Node{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.textContent='';this.className='';this.hidden=false;this.disabled=false;this.value='';this.open=false;this.listeners={};this.dataset={};this.nextElementSibling={textContent:''};this.previousElementSibling={textContent:''};this.classList={toggle:(key,on)=>{const s=new Set(this.className.split(' ').filter(Boolean));on?s.add(key):s.delete(key);this.className=[...s].join(' ');}};}
  append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=[...nodes];}setAttribute(){}removeAttribute(){}focus(){}select(){}reset(){}showModal(){this.open=true;}close(){this.open=false;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  querySelector(selector){this.queries??={};return this.queries[selector]??=new Node();}
  querySelectorAll(selector){if(selector==='p')return [this.querySelector('p0'),this.querySelector('p1')];return [];}
}
const nodes=new Map();const get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
const known={downloaded:100,uploaded:50,total:150,available:true,connected:null};
let fixture={version:'0.4.0',demo:false,profiles:[{id:'a',name:'Allowed offline',url:'https://cloud.mail.ru/public/Test/A',transport:'mailru',state:'inactive',access_enabled:true,user_id:'u-a',usage:known},{id:'b',name:'Disabled',url:'https://cloud.mail.ru/public/Test/B',transport:'mailru',state:'inactive',access_enabled:false,user_id:'u-b',usage:{total:null}}],happ:[{id:'h-a',name:'Happ',port:8443,flow:'',access_enabled:false,user_id:'u-h',manageable:true,usage:known}],happ_ready:true,happ_error:'',users:[{id:'u-a',name:'A',devices:['a'],complete:true,available:true,...known},{id:'u-b',name:'B',devices:['b'],complete:false,total:0},{id:'u-h',name:'H',devices:['h-a'],complete:true,available:true,...known}],memory:{available:200000000,total:900000000},max_profiles:10,updated:0,error:''};
let fail=false;const requests=[];
const sandbox={document:{hidden:false,getElementById:get,createElement:tag=>new Node(tag),querySelector:()=>({content:'test-token'}),querySelectorAll:()=>[]},window:{confirm:()=>true},navigator:{clipboard:{writeText:async()=>{}}},fetch:async(path,opt)=>{requests.push({path,opt});return {ok:!fail,json:async()=>fail?{error:'synthetic server failure'}:fixture};},setInterval:()=>0,setTimeout:()=>0,clearTimeout:()=>{},Date,console};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8'),sandbox);
function text(node){return [node.textContent,...node.children.map(text)].join(' ');}
(async()=>{
  await new Promise(setImmediate);
  fixture.users.find(u=>u.id==='u-b').total=null;
  vm.runInContext('render(state)',sandbox);
  assert.ok(!nodes.has('owners'));
  assert.equal(get('rows').children.length,2);
  assert.match(text(get('rows').children[0]),/Доступ разрешён/);
  assert.doesNotMatch(text(get('rows').children[0]),/Отключён/);
  assert.match(get('rows').children[1].className,/disabled-user/);
  assert.match(text(get('rows').children[1]),/Отключён.*Статистика недоступна.*Включить доступ/);
  assert.match(text(get('rows').children[0]),/Скачано: 100 Б.*Передано: 50 Б.*Всего: 150 Б/);
  assert.ok(!nodes.has('updated'));
  get('nav-happ').listeners.click();
  assert.equal(get('section-title').textContent,'Happ VPN');
  assert.match(get('rows').children[0].className,/disabled-user/);
  assert.match(text(get('rows').children[0]),/Включить доступ/);
  fixture.happ[0].access_enabled=true;
  vm.runInContext('render(state)',sandbox);
  assert.doesNotMatch(get('rows').children[0].className,/disabled-user/);
  assert.match(text(get('rows').children[0]),/Отключить доступ/);
  vm.runInContext("card({...state.happ[0],kind:'happ'})",sandbox);
  assert.ok(!nodes.has('device-owner-name'));
  assert.ok(!nodes.has('device-owner'));
  assert.equal(get('device-owner-summary').hidden,true);
  get('device-name').value='Laptop';get('device-flow').value='xtls-rprx-vision';
  requests.length=0;
  get('device-form').listeners.submit({preventDefault(){}});
  await new Promise(setImmediate);
  const rename=requests.find(r=>r.opt?.method==='POST');
  assert.equal(rename.path,'/api/happ/h-a/labels');
  assert.deepEqual(JSON.parse(rename.opt.body),{name:'Laptop'});
  assert.ok(!requests.some(r=>r.path.endsWith('/edit')));
  fixture.users.find(u=>u.id==='u-h').devices=['h-a','other-device'];
  vm.runInContext("card({...state.happ[0],kind:'happ'})",sandbox);
  assert.equal(get('device-owner-summary').hidden,false);
  assert.match(get('device-owner-count').textContent,/устройств: 2/);
  vm.runInContext("card({...state.profiles[1],kind:'openflux'})",sandbox);
  assert.match(text(get('device-usage')),/Статистика недоступна/);
  requests.length=0;
  vm.runInContext("deleteDialog({...state.happ[0],kind:'happ'})",sandbox);
  assert.ok(!nodes.has('delete-confirm'));
  assert.equal(get('delete-dialog').open,true);
  assert.equal(requests.length,0);
  get('delete-dialog').close();
  assert.equal(requests.length,0); // Cancel never revokes credentials.
  vm.runInContext("deleteDialog({...state.happ[0],kind:'happ'})",sandbox);
  get('delete-form').listeners.submit({preventDefault(){}});
  await new Promise(setImmediate);
  const removal=requests.find(r=>r.opt?.method==='POST');
  assert.equal(removal.path,'/api/happ/h-a/delete');
  assert.deepEqual(JSON.parse(removal.opt.body),{confirm_name:'Happ'});
  const before=text(get('rows').children[0]);fail=true;
  await vm.runInContext("perform(document.getElementById('refresh'),()=>control({...state.happ[0],kind:'happ'},'stop'))",sandbox);
  assert.match(get('error').textContent,/synthetic server failure/);
  assert.equal(text(get('rows').children[0]),before);
  assert.doesNotMatch(get('toast').textContent,/Доступ отключён/);
  fail=false;
  fixture.amnezia=[{id:'a-awg',name:'Amnezia old',kind:'amnezia',access_enabled:false,exportable:false,user_id:'u-awg',address:'10.8.1.2/32',manageable:true,usage:known}];
  fixture.amnezia_ready=true;fixture.amnezia_info={limitation:'Строгая привязка недоступна'};
  vm.runInContext('render(state)',sandbox);get('nav-amnezia').listeners.click();
  assert.equal(get('section-title').textContent,'Amnezia VPN');
  assert.match(text(get('rows').children[0]),/AmneziaWG.*Отключён/);
  assert.match(get('rows').children[0].className,/disabled-user/);
  assert.match(text(get('rows').children[0]),/приватный ключ хранится только у клиента/);
  assert.doesNotMatch(text(get('rows').children[0]),/Перезапуск/);
  assert.equal(vm.runInContext("endpoint({kind:'amnezia',id:'a-awg'})",sandbox),'/api/amnezia/a-awg');
  vm.runInContext("card({...state.amnezia[0],kind:'amnezia'})",sandbox);
  assert.match(get('device-params').textContent,/10.8.1.2/);
  assert.equal(get('device-flow-field').hidden,true);
  fixture={name:'Native',link:'synthetic-link',secret:'synthetic-secret',client_delivery:'native',requires_transport_selection:false,svg:'synthetic-image'};
  await vm.runInContext("share({id:'a',kind:'openflux',transport:'mailru'})",sandbox);
  assert.equal(get('qr-native-mail').hidden,false);
  assert.ok(!nodes.has('qr-polling'));
  assert.match(get('qr-dialog').querySelector('p').textContent,/сканирование QR/);
  get('qr-dialog').listeners.close();
  assert.equal(get('share-key').value,'');
  fixture={name:'Old',link:'synthetic-link',client_delivery:'native',svg:'synthetic-image'};
  await vm.runInContext("share({id:'a',kind:'openflux'})",sandbox);
  assert.equal(get('qr-native-mail').hidden,true);
  get('transport').value='telemost';vm.runInContext('updateTransportHint()',sandbox);
  assert.match(get('url-label').textContent,/конференция/);
  assert.match(get('document-hint').textContent,/Без Direct/);
  assert.equal(get('yandex-hint').hidden,true);
  assert.match(vm.runInContext("typeName({transport:'telemost'})",sandbox),/Телемост/);
  await vm.runInContext("share({id:'t',kind:'openflux',transport:'telemost'})",sandbox);
  assert.equal(get('qr-telemost').hidden,false);
  assert.equal(get('qr-native-mail').hidden,true);
  get('qr-dialog').listeners.close();assert.equal(get('qr-telemost').hidden,true);
  console.log('PASS: existing UI regression tests; native Mail.ru notice; polling UI absent; secrets cleared on close');
})().catch(error=>{console.error(error);process.exitCode=1;});

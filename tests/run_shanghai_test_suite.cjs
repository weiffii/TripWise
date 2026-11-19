'use strict';
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const results=[];
for(const name of fs.readdirSync(__dirname).filter(n=>/^test_shanghai.*\.cjs$/.test(n)&&!n.includes('browser')).sort()){
 const start=Date.now(),r=cp.spawnSync(process.execPath,[path.join(__dirname,name)],{encoding:'utf8',timeout:120000,maxBuffer:4*1024*1024});
 const item={file:name,exit_code:r.status,elapsed_ms:Date.now()-start,error:r.error?String(r.error):null,stdout:r.stdout,stderr:r.stderr};results.push(item);console.log(JSON.stringify({file:name,exit_code:r.status,elapsed_ms:item.elapsed_ms,error:item.error}));
}
fs.mkdirSync(path.resolve(__dirname,'../reports/benchmarks'),{recursive:true});
fs.writeFileSync(path.resolve(__dirname,'../reports/benchmarks/shanghai-5000-js-tests.json'),JSON.stringify({node:process.version,results},null,2)+'\n');
process.exitCode=results.some(r=>r.exit_code!==0)?1:0;

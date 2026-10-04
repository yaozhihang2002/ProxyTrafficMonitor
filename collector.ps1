param([string]$SettingsPath=(Join-Path $PSScriptRoot 'data\settings.json'),[switch]$Once)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$config=Join-Path $env:APPDATA 'io.github.clash-verge-rev.clash-verge-rev\clash-verge.yaml'
function PipeSnapshot($name) {
 if(!$name){throw 'Mihomo 管道未找到，请确认 Clash Verge 正在运行'}
 $pipe=[IO.Pipes.NamedPipeClientStream]::new('.',$name,[IO.Pipes.PipeDirection]::InOut,[IO.Pipes.PipeOptions]::Asynchronous)
 try {
  $pipe.Connect(2000)
  $request="GET /connections HTTP/1.1`r`nHost: localhost`r`nConnection: close`r`n"
  $bytes=[Text.Encoding]::ASCII.GetBytes($request+"`r`n")
  $pipe.Write($bytes,0,$bytes.Length)
  $stream=[IO.MemoryStream]::new(); $buffer=[byte[]]::new(65536)
  try {
   while($true){$task=$pipe.ReadAsync($buffer,0,$buffer.Length); if(!$task.Wait(4000)){throw 'Mihomo 读取超时'}; $n=$task.Result; if(!$n){break}; $stream.Write($buffer,0,$n); if($stream.Length -gt 33554432){throw 'Mihomo 快照超过限制'}}
   $raw=$stream.ToArray()
  } finally {$stream.Dispose()}
  # Latin1 preserves byte offsets while parsing HTTP framing.
  $wire=[Text.Encoding]::GetEncoding(28591).GetString($raw); $sep=$wire.IndexOf("`r`n`r`n")
  if($sep -lt 0 -or $wire -notmatch '^HTTP/1\.[01] 200 '){throw 'Mihomo HTTP 响应异常'}
  $headers=$wire.Substring(0,$sep); $body=$wire.Substring($sep+4)
  if($headers -match '(?im)^Transfer-Encoding:\s*chunked'){
   $decoded=[Text.StringBuilder]::new(); $offset=0
   while($true){$end=$body.IndexOf("`r`n",$offset); if($end -lt 0){throw 'Chunk header incomplete'}; $size=[Convert]::ToInt32(($body.Substring($offset,$end-$offset) -split ';')[0],16); if(!$size){break}; $offset=$end+2; if($offset+$size -gt $body.Length){throw 'Chunk incomplete'}; [void]$decoded.Append($body.Substring($offset,$size));$offset+=$size+2}
   $body=$decoded.ToString()
  }
  $json=[Text.Encoding]::UTF8.GetString([Text.Encoding]::GetEncoding(28591).GetBytes($body))|ConvertFrom-Json
  return $json
 } finally {$pipe.Dispose()}
}
function HttpSnapshot($endpoint,$secret) {
 $handler=[Net.Http.HttpClientHandler]::new();$handler.UseProxy=$false;$handler.AllowAutoRedirect=$false
 $client=[Net.Http.HttpClient]::new($handler);$client.Timeout=[TimeSpan]::FromSeconds(4)
 try {
  if($secret){$client.DefaultRequestHeaders.Authorization=[Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer',$secret)}
  $response=$client.GetAsync($endpoint.TrimEnd('/')+'/connections',[Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
  if(!$response.IsSuccessStatusCode){throw "控制接口返回 HTTP $([int]$response.StatusCode)，请检查端口和 Secret"}
  $stream=$response.Content.ReadAsStreamAsync().GetAwaiter().GetResult();$ms=[IO.MemoryStream]::new();$buf=[byte[]]::new(65536)
  try {while($true){$read=$stream.ReadAsync($buf,0,$buf.Length);if(!$read.Wait(4000)){throw 'HTTP 读取超时'};$n=$read.Result;if(!$n){break};$ms.Write($buf,0,$n);if($ms.Length -gt 33554432){throw '连接快照超过 32 MiB 限制'}};$text=[Text.Encoding]::UTF8.GetString($ms.ToArray())}finally{$ms.Dispose();$stream.Dispose()}
  $result=$text|ConvertFrom-Json
  if(!$result.PSObject.Properties['connections'] -or !$result.PSObject.Properties['downloadTotal']){throw '该端口不是兼容的 Clash 连接统计接口'}
  return $result
 }finally{$client.Dispose();$handler.Dispose()}
}
Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.Security
while($true){
 $clock=[Diagnostics.Stopwatch]::StartNew()
 try {
  $preferences=if(Test-Path -LiteralPath $SettingsPath){Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8|ConvertFrom-Json}else{[pscustomobject]@{mode='auto';endpoint='http://127.0.0.1:9090';pipe='';interval=1}}
  $secret=''
  if($preferences.secret_dpapi){$secret=[Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($preferences.secret_dpapi),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser))}
  $name=[string]$preferences.pipe
  if(!$name -and $preferences.mode -ne 'http'){
   $names=@([IO.Directory]::GetFiles('\\.\pipe\')|Where-Object {$_ -match 'verge-mihomo-production-|\\mihomo$|\\clash$'})
   if($names.Count -gt 1){throw '发现多个代理核心，请在设置中指定管道名或 HTTP 地址'}
   if($names.Count){$name=$names[0] -replace '^.*\\',''}
  }
  if($preferences.mode -eq 'pipe' -or ($preferences.mode -eq 'auto' -and $name)){$snap=PipeSnapshot $name;$source='pipe:'+$name}
  else{$snap=HttpSnapshot $preferences.endpoint $secret;$source='http:'+$preferences.endpoint}
  $proxy=@($snap.connections|Where-Object {$_.chains.Count -gt 0 -and $_.chains -notcontains 'DIRECT' -and $_.chains -notcontains 'REJECT' -and $_.chains -notcontains 'REJECT-DROP'})
  $ports=@{}
  netstat -ano | ForEach-Object {
   if($_ -match '^\s*(TCP|UDP)\s+(\S+):(\d+)\s+.*\s+(\d+)\s*$'){
    $key=$Matches[1].ToLower()+':'+$Matches[3]; $entry=@{address=$Matches[2].Trim('[',']');pid=[int]$Matches[4]}
    if(!$ports.ContainsKey($key)){$ports[$key]=@()}; $ports[$key]+=$entry
   }
  }
  # A fresh process snapshot prevents stale PID reuse from being labelled as a previous program.
  $processes=@{}; Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId,Name,ExecutablePath,CreationDate | ForEach-Object {$processes[[int]$_.ProcessId]=$_}
  $rows=@(foreach($c in $proxy){
   $m=$c.metadata; $entries=@($ports[([string]$m.network).ToLower()+':'+$m.sourcePort])
   $exact=@($entries|Where-Object {$_.address -eq $m.sourceIP})
   if($exact.Count){$entries=$exact}else{$entries=@($entries|Where-Object {$_.address -eq '0.0.0.0' -or $_.address -eq '::'})}
   $ids=@($entries|ForEach-Object {$_.pid}|Sort-Object -Unique)
   $ownerId=0; $binding='unknown'
   if($ids.Count -eq 1){$ownerId=[int]$ids[0];$binding='socket'}elseif($ids.Count -gt 1){$binding='ambiguous'}
   $p=if($ownerId -gt 0){$processes[$ownerId]}else{$null}; $parent=if($p -and $p.ParentProcessId -gt 0){$processes[[int]$p.ParentProcessId]}else{$null}
   $startText=if($c.start -is [datetime]){$c.start.ToUniversalTime().ToString('o')}else{[string]$c.start}
   $born=[DateTimeOffset]::MinValue
   if($p -and [DateTimeOffset]::TryParse($startText,[ref]$born) -and $p.CreationDate.ToUniversalTime() -gt $born.UtcDateTime){$p=$null;$parent=$null}
   if($parent -and $p.CreationDate -and $parent.CreationDate -gt $p.CreationDate){$parent=$null}
   if(!$p){$ownerId=0;$binding='unknown'}
   $path=if($p.ExecutablePath){$p.ExecutablePath}else{[string]$m.processPath}
   $procName=if($p.Name){$p.Name}else{[string]$m.process}
   if(!$ownerId -and $procName){$binding='core-name-only'}
   [ordered]@{id=[string]$c.id;start=$startText;host=[string]$m.host;ip=[string]$m.destinationIP;port=[string]$m.destinationPort;network=[string]$m.network;source_ip=[string]$m.sourceIP;source_port=[string]$m.sourcePort;pid=$ownerId;process=$procName;path=$path;created=if($p.CreationDate){$p.CreationDate.ToUniversalTime().ToString('o')}else{''};parent=if($parent){[string]$parent.Name}else{''};parent_path=if($parent){[string]$parent.ExecutablePath}else{''};binding=$binding;chains=@($c.chains);rule=[string]$c.rule;rule_payload=[string]$c.rulePayload;down=[long]$c.download;up=[long]$c.upload}
  })
  [Console]::WriteLine(([ordered]@{time=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0;source=$source;rows=$rows;unclassified=@($snap.connections|Where-Object {!$_.chains.Count}).Count;duration_ms=$clock.ElapsedMilliseconds}|ConvertTo-Json -Depth 6 -Compress))
 } catch {
  # Never emit raw config, credentials, or connection payloads.
  [Console]::WriteLine((@{error=($_.Exception.Message -replace '[\r\n]+',' ');time=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0}|ConvertTo-Json -Compress))
 }
 if($Once){break}
 $interval=if($preferences.interval){[int]$preferences.interval}else{1}
 $delay=[math]::Max(100,$interval*1000-$clock.ElapsedMilliseconds); Start-Sleep -Milliseconds $delay
}


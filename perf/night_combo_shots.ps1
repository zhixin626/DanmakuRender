$ErrorActionPreference = 'Stop'
$dir = (Get-ChildItem -Directory | ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Directory -ErrorAction SilentlyContinue } | Where-Object Name -like '2026-11-11_13*' | Select-Object -First 1).FullName
$video = (Get-ChildItem -LiteralPath $dir -Filter '*.mp4' | Select-Object -First 1).FullName
ffmpeg -y -v error -ss 9.9 -i $video -frames:v 1 "$dir/09_9.9s.png"
ffmpeg -y -v error -ss 10.1 -i $video -frames:v 1 "$dir/10_10.1s.png"
ffmpeg -y -v error -ss 12.9 -i $video -frames:v 1 "$dir/12_12.9s.png"
ffmpeg -y -v error -ss 13.1 -i $video -frames:v 1 "$dir/13_13.1s.png"
ffmpeg -y -v error -ss 15.9 -i $video -frames:v 1 "$dir/15_15.9s.png"
ffmpeg -y -v error -ss 16.1 -i $video -frames:v 1 "$dir/16_16.1s.png"

$ErrorActionPreference = 'Stop'
$dir = (Get-ChildItem -Directory | ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Directory -ErrorAction SilentlyContinue } | Where-Object Name -like '2026-11-11_14*' | Select-Object -First 1).FullName
$videos = @(Get-ChildItem -LiteralPath $dir -Filter '*.mp4' | Sort-Object Name)
$e = $videos[0].FullName
$g = $videos[$videos.Count - 1].FullName
$times = @(
  @('01_push', '4.8', '7.2'), @('02_fall', '19.8', '22.2'),
  @('03_entry', '34.8', '35.4'), @('04_multi', '49.8', '51.2'),
  @('05_different', '64.8', '67.2'), @('06_boundary', '79.8', '85.3'),
  @('07_long', '94.8', '99.9'), @('08_count10', '109.8', '112.2'),
  @('09_tiers', '124.8', '129.2'), @('10_small', '139.8', '143.2'),
  @('11_tid', '154.8', '158.2'), @('12_pile', '171.8', '177.2')
)
foreach($item in $times){
  $name=$item[0]; $before=$item[1]; $after=$item[2]
  foreach($pair in @(@('before',$before),@('after',$after))){
    $label=$pair[0]; $time=$pair[1]
    $out=Join-Path $dir ("$name`_$label`_E-G_放大.png")
    $left = Join-Path $dir "_left.png"
    $right = Join-Path $dir "_right.png"
    ffmpeg -y -v error -ss $time -i $e -frames:v 1 $left
    ffmpeg -y -v error -ss $time -i $g -frames:v 1 $right
    ffmpeg -y -v error -i $left -i $right -filter_complex "[0:v]crop=800:800:140:1000,scale=1200:1200[left];[1:v]crop=800:800:140:1000,scale=1200:1200[right];[left][right]hstack=inputs=2" -frames:v 1 $out
    Remove-Item -Force $left,$right
  }
}

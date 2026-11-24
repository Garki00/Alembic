// packaging/tools/sync-version.mjs
// 把发布版本写入 package.json / package-lock.json，避免版本号在多处漂移。
//
// 用法：node packaging/tools/sync-version.mjs 0.2.0 desktop/package.json desktop/package-lock.json installer/package.json
import fs from "node:fs";

const [version, ...files] = process.argv.slice(2);
if (!version || !/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(version)) {
  console.error(`无效版本号：${version || "(空)"}`);
  process.exit(2);
}
if (!files.length) {
  console.error("至少需要一个 package.json 路径");
  process.exit(2);
}

for (const file of files) {
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  data.version = version;
  // npm lockfile v3 在根和 packages[""] 各存一份版本。
  if (data.packages && data.packages[""]) data.packages[""].version = version;
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n", "utf8");
  console.log(`版本已更新：${file} → ${version}`);
}

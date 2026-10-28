# Organization hub registry sync

ชุดคำสั่งนี้อัปเดต metadata จาก GitHub ให้ hub ของแต่ละ Org และ personal hub
พร้อมตรวจลิงก์ของ umbrella โดยเก็บทะเบียนไว้ใน repo เจ้าของแต่ละแห่ง

## การเรียกใช้งาน

รันจาก checkout ของ `bookchaowalit/platform-ops-cli` และระบุที่อยู่ของทะเบียน
กับโฟลเดอร์ที่เก็บ hub โดยตรง ใช้ Python standard library, Git และ GitHub CLI
ที่ล็อกอินด้วยบัญชีเจ้าของอยู่แล้ว

```bash
task hubs:sync -- --index /home/bookchaowalit/bookchaowalit-empire/registry/hubs.json --checkouts-root /home/bookchaowalit
task hubs:sync -- --index /home/bookchaowalit/bookchaowalit-empire/registry/hubs.json --checkouts-root /home/bookchaowalit --check
task hubs:sync -- --index /home/bookchaowalit/bookchaowalit-empire/registry/hubs.json --checkouts-root /home/bookchaowalit --apply --publish
```

- ค่าเริ่มต้นแสดง preview; `--check` ออกด้วย code 1 เมื่อมีข้อมูลต่างจาก GitHub
- `--apply` อัปเดตไฟล์และ commit เฉพาะไฟล์ที่กำหนด; `--publish` จึงจะ push ด้วย
- Code 2 หมายถึงมีสิ่งที่ต้องแก้ เช่น checkout มีงานค้าง, branch/remote ไม่ตรง,
  สิทธิ์ Org เปลี่ยน, GitHub อ่านไม่ได้ หรือมี Org ใหม่ที่ยังไม่ลงทะเบียน
- ถ้าข้อมูลเหมือนเดิม จะคง snapshot date และไม่สร้าง commit

## ขอบเขตข้อมูล

Umbrella เก็บเฉพาะ hub metadata และตำแหน่งไฟล์ projection ในแต่ละ hub ผ่าน
`organizations[].sync.inventory_path` และ `organizations[].sync.readme_path`

`personal_hub.sync` เป็น block ที่ไม่บังคับ ใช้กฎ path และ symlink เดียวกับ Org
ถ้ามี worker จะอ่าน `/user/repos?affiliation=owner` แล้วเก็บเฉพาะ repo ที่
`owner.login` ตรงกับเจ้าของ personal hub (`bookchaowalit/personal-hub`)
ทะเบียนใช้ schema เดียวกับ Org โดย field `organization` เก็บชื่อบัญชีส่วนตัว และ
`inventory_scope` ระบุว่าเป็น repo ของบัญชีส่วนตัว ส่วน repo ของ Org อยู่ใน hub ของ
Org นั้น ถ้าไม่มี `sync` personal hub จะเป็นเพียง reference ที่ตรวจ metadata
ชื่อ repo ที่ขึ้นต้นด้วยจุด เช่น `bookchaowalit/.github` ใช้ได้เฉพาะส่วนชื่อ repo
ส่วนชื่อ owner และ `checkout_name` ยังห้ามขึ้นต้นด้วยจุด

Org hubs และ personal hub ใช้ทะเบียน `repository-catalog/registries/repositories.json` และส่วน
`ORG-HUB INVENTORY` ใน README ส่วน Work Hub ใช้
`repository-catalog/registries/github-repositories.json` กับ
`repository-catalog/GITHUB-REPOSITORIES.md` แยกจาก client registry เดิม
ทะเบียน GitHub นี้แสดง repo ที่อยู่ใน Org ตาม provider; client registry อาจอ้างถึง
repo ของลูกค้าที่มี owner อื่นด้วย จึงมีขอบเขตต่างกัน

Worker อ่านเฉพาะ GitHub repository metadata และไฟล์ projection ที่ตั้งค่าไว้
เก็บ archived/private repos และ default branch ตาม GitHub โดยไม่รวม hub เอง
ตรวจ owner, URL, path, symlink และ marker ก่อนเขียน ไม่อ่านเนื้อหา product หรือ
เอกสารลูกค้า และไม่รวบรวม product records มาเก็บใน umbrella

## ตัวตั้งเวลาแบบ hosted

ตั้งแต่ 2026-10-28 งานประจำวันรันบน GitHub Actions ของ repo นี้ (public จึงใช้
GitHub-hosted runner) workflow `.github/workflows/org-hub-sync.yml` ชื่อ
**Org hub sync** เวลา **09:00 ทุกวัน Asia/Bangkok** (`0 2 * * *` UTC) และกดรันเองได้

```bash
gh workflow run org-hub-sync.yml --repo bookchaowalit/platform-ops-cli
gh run list --workflow org-hub-sync.yml --repo bookchaowalit/platform-ops-cli --limit 5
```

แต่ละ run เริ่มจากเครื่องว่าง: clone `bookchaowalit-empire` เข้า `$RUNNER_TEMP/hubs`
แล้วรัน worker ด้วย `--clone-missing --apply --publish` worker จะ clone hub ที่ยังไม่มี
checkout (default branch, single branch) ก่อนตรวจตามกฎเดิมทุกข้อ ไม่แตะ checkout ที่มีอยู่แล้ว

Credential คือ environment secret `ORG_HUB_SYNC_TOKEN` ใน environment `org-hub-sync`
(อนุญาตเฉพาะ branch `main`) ตั้งจาก Infisical `/github` ด้วย stdin โดยไม่พิมพ์ค่า
ต้องเป็น classic token ของบัญชีเจ้าของ (worker ตรวจ `/user` ว่าเป็น `bookchaowalit`)
ที่มี `repo` และ `read:org` เพราะ fine-grained token ครอบได้ owner เดียว ส่วน Org
อื่นที่บัญชีเป็นสมาชิก เช่น Happy-Teeth อยู่นอกทะเบียนและ worker ไม่แตะ

```bash
source ~/.config/solo-empire/environment.sh
infisical run --projectId "$INFISICAL_PROJECT_ID" --env dev --path /github --silent -- \
  bash -c 'printf %s "$GITHUB_PERSONAL_ACCESS_TOKEN" | gh secret set ORG_HUB_SYNC_TOKEN --env org-hub-sync --repo bookchaowalit/platform-ops-cli'
```

Log ของ run แสดงเฉพาะสถานะต่อ target (`unchanged`, `updated` หรือ `blocked` พร้อม code)
และ job ล้มเมื่อมี target ที่ blocked ซึ่ง GitHub แจ้งเจ้าของทางอีเมล รายงานเต็ม
ไม่ถูกเผยแพร่เพราะ repo นี้ public Commit ใช้ชื่อ `bookchaowalit-org-hub-sync`

## ตัวตั้งเวลาในเครื่อง (ปิดแล้ว)

User systemd timer `bookchaowalit-org-hub-sync.timer` เดิมถูกปิดหลัง hosted run แรก
ผ่าน `task hubs:timer` ยังใช้ติดตั้งใหม่ได้ในกรณีฉุกเฉิน แต่ห้ามเปิดคู่กับ workflow
เพราะ lock ป้องกัน writer ซ้อนได้เฉพาะในเครื่องเดียวกัน

```bash
systemctl --user list-timers bookchaowalit-org-hub-sync.timer
journalctl --user -u bookchaowalit-org-hub-sync.service -n 20 --no-pager
```

สถานะของ run ในเครื่องอยู่ที่ `.runtime/org-hub-sync.json` ใน umbrella ซึ่งถูก Git ignore
ไม่มี token หรือเนื้อหาเอกสารอยู่ในรายงาน

## เมื่อมีงานค้างหรือ publish ไม่ผ่าน

Worker จะข้าม hub ที่มี working tree dirty หรือ branch/remote ไม่ตรง และรายงาน
ความสำเร็จบางส่วนให้เห็นชัดเจน Checkout ที่สะอาดและตามหลังอย่างเดียวสามารถ
fast-forward ได้ใน apply mode ส่วน unpushed/diverged commits ต้องให้เจ้าของจัดการ
ถ้า push ถูกปฏิเสธ worker จะเก็บ local commit ไว้และรายงาน `git_push_failed`
ตรวจสถานะและประสานงานที่ repo เจ้าของก่อนรันใหม่ ห้าม force-push หรือทิ้งงานค้าง

เมื่อต้องหยุดงานอัตโนมัติ:

```bash
gh workflow disable org-hub-sync.yml --repo bookchaowalit/platform-ops-cli
```

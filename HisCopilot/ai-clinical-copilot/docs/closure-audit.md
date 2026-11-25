# 闭环验收审计 (updatev2_3 §6)

**日期**: 2026-10-15  
**验收人**: 自动化 E2E 测试  
**项目**: 象元 AI 诊疗  
**版本**: updatev2_3（在 updatev2_2 基础上增量）

---

## §1 统一诊断与综合治疗方案

### 根因
原架构为 **per-diagnosis** 模式：`POST /api/encounters/{eid}/diagnoses/{did}/treatment` 为每个诊断创建独立 `PrescriptionDraft`（`target_diagnosis_id=did`），前端为每个 accepted 诊断渲染独立"生成治疗方案"按钮 + 独立 `PrescriptionForm`。这导致 N 个诊断 → N 个治疗草稿，药物可能重复/冲突，不符合"每次就诊只生成一份综合治疗方案"的要求。

### 修复
| 测试项 | 结果 | 说明 |
|--------|------|------|
| 新增 `generate_treatment_for_encounter` (orchestrator) | ✅ | 接收 list[diagnoses]，一次 LLM 调用生成统一方案 |
| 新增 prompt `prompts/treatment_for_encounter.md` | ✅ | 多诊断输入，"统筹处理全部已确认诊断，合并同类药物、剔除重复与配伍禁忌" |
| `POST /api/encounters/{eid}/treatment` 端点 | ✅ | 收集 accepted/modified 诊断，创建单一 PrescriptionDraft (target_diagnosis_id="") |
| **幂等性**：重复点击不产生重复草稿 | ✅ | 相同 snapshot hash → `reused: true`，返回已有 draft |
| `diagnosis_snapshot_hash` 诊断版本追踪 | ✅ | SHA256(sorted diagnoses id+module+western_diagnosis+tcm_syndrome+review_status) |
| 诊断变化 → 旧方案标记过期 | ✅ | `mark_stale_for_encounter` 在 `update_diagnosis` 中触发；旧 draft `is_stale=True` |
| `is_stale` / `diagnosis_snapshot_hash` 列迁移 | ✅ | `ALTER TABLE prescription_drafts ADD COLUMN` 幂等迁移 |
| 诊断变化后重新生成 → 新草稿 | ✅ | E2E: rx-d3e8b270 (stale) → rx-496bc724 (fresh)，snapshot hash 变化 |
| `GET /api/encounters/{eid}/treatment/latest` | ✅ | 返回最新非 stale 统一 draft + stale 标记 |
| SIGNED/SUBMITTED 草稿不被标记 stale | ✅ | `mark_stale_for_encounter` 跳过终端状态 |

### E2E 实测
```
rx-d3e8b270: AI_GENERATED, is_stale=1, target_diagnosis_id=''  (旧，诊断变化后过期)
rx-496bc724: SUBMITTED_TO_HIS, is_stale=0, target_diagnosis_id=''  (新，当前有效)
```
重复生成返回 `{"prescription_id":"rx-496bc724","reused":true}`。

---

## §2 正式病历、治疗方案与处方的关系

### 根因
1. `MockHISAdapter._prescriptions` 是内存 list，重启丢失——处方数据无持久化。
2. 无独立 e-prescription 表，仅依赖内存。
3. `MedicalRecordRepo.upsert_for_encounter` 忽略 `doctor_id`；`get_by_encounter` 不返回 status/finalized_at。
4. `finalize_medical_record` 取**第一个** SIGNED draft，不合并、不支持统一方案。
5. `encounters.final_prescription_id` 列在迁移中存在但 ORM model 无此字段。
6. PDF 导出仅是前端 `window.print()` 链接，无服务端真实 PDF 生成。

### 修复
| 测试项 | 结果 | 说明 |
|--------|------|------|
| 新增 `EPrescription` 表 | ✅ | id/encounter_id/patient_id/doctor_id/prescription_draft_id/doc_type/diagnosis_summary/prescription_json/signature_id/doctor_name/his_status/request_id/created_at |
| `EPrescriptionRepo` (create/get/list_for_encounter/list_for_patient) | ✅ | create 幂等：encounter+draft+doc_type+signature_id |
| `submit_his` 持久化电子处方到 DB | ✅ | 西药/成药→western_drugs；每个 herbal_draft→herbal_decoction；therapy→therapy_orders |
| **空 prescription 回退**：从 SIGNED draft 取真实处方 | ✅ | 修复：调用方传空 prescription 时从 draft.structured_json 取 |
| Encounter ORM 添加 `final_prescription_id` | ✅ | 迁移已存在，补 ORM 声明 |
| `MedicalRecordRepo.upsert_for_encounter` 传 doctor_id | ✅ | |
| `MedicalRecordRepo.get_by_encounter` 返回完整字段 | ✅ | status/finalized_at/doctor_id/signature_id/followup_plan_id/updated_at |
| `finalize_medical_record` 查找统一 draft | ✅ | `get_unified_draft` 优先，legacy per-diagnosis 回退 |
| finalize 纳入所有诊断 + 统一方案 + e-prescriptions | ✅ | content_json 完整组装 |
| `GET /api/encounters/{eid}/prescriptions` | ✅ | 查该就诊电子处方列表 |
| `GET /api/prescriptions/{epid}` | ✅ | 单条电子处方详情 |
| **真实 PDF 导出** (fpdf2 + CJK 字体) | ✅ | `_build_pdf_bytes()` 服务端生成 |
| `GET /api/medical-records/{mid}/pdf` | ✅ | HTTP 200, 62262 bytes, PDF v1.6 |
| `GET /api/prescriptions/{epid}/pdf` | ✅ | HTTP 200, 28349 bytes, PDF v1.6 |
| **幂等 submit**：已 SUBMITTED_TO_HIS 不报错 | ✅ | 修复：重复 submit 返回成功而非 409 |

### E2E 实测
```
e_prescriptions count=3
  [0] therapy_orders   his=ACCEPTED doctor=张明 主任医师
  [1] herbal_decoction  his=ACCEPTED doctor=张明 主任医师
  [2] western_drugs     his=ACCEPTED doctor=张明 主任医师
encounter.final_prescription_id = f5d85d668de34ee29c4f93f0
```

---

## §3 修复患者信息与就诊事实为空

### 根因
1. 患者列表用 `/mock-his/patients` 且读 `p.sex`，但后端返回 `gender` → 性别列恒空。
2. `[eid]/page.tsx` 有合成 pid fallback `p-${eid.replace("enc-","")}` → 患者永远 null。
3. 未填写字段无明确提示（显示"—"或空白）。

### 修复
| 测试项 | 结果 | 说明 |
|--------|------|------|
| 患者列表改用 `/api/patients` | ✅ | 不再依赖 mock-his |
| `p.sex` → `p.gender` | ✅ | 字段映射修正 |
| 空值显示"未填写" | ✅ | 姓名/性别/年龄/主诉/现病史/过敏史/症状/慢病/当前用药/检验 |
| 移除合成 pid fallback | ✅ | encounter 加载失败显式报错 |
| intake→/api/patients→PatientRepo.upsert 字段映射 | ✅ | name/gender/age/phone 写入 |
| chief_complaint/raw_text/history→EncounterRepo.upsert | ✅ | 就诊事实持久化 |

### E2E 实测
```
patient: name=王测试 gender=男 age=35 phone=13900002023 allergies=[{青霉素/明确}] insurance=医保
encounter: pid=p-632c9457 doctor=dr-001 cc=反复头痛3月... source=doctor status=PENDING
```

---

## §4 患者列表、今日接诊和临床诊疗室

### 根因
1. 侧栏"今日接诊"和"临床诊疗室"都指向 `/encounters` → 按钮重叠。
2. 无患者详情页。
3. `/encounters?pid=` 参数被忽略。

### 修复
| 测试项 | 结果 | 说明 |
|--------|------|------|
| 新建患者详情页 `/patients/[pid]` | ✅ | 患者档案 + 历史就诊时间线 |
| 每条就诊："查看本次就诊" + "新建就诊"按钮 | ✅ | → /encounters/{eid} 和 /encounters/new?pid={pid} |
| 今日接诊 honor `?pid=` 参数 | ✅ | `useSearchParams()` 读取 + 客户端过滤 |
| 有 pid 时标题"就诊列表（按患者筛选）" | ✅ | |
| 侧栏"临床诊疗室" → `/patients` | ✅ | 避免与"今日接诊"重复 |
| 新建就诊预填患者 (`?pid=`) | ✅ | `useEffect` 预加载患者数据 |
| 患者行 Link → `/patients/${p.id}` | ✅ | |

---

## §5 患者端与医生端

| 测试项 | 结果 | 说明 |
|--------|------|------|
| 患者端 intake 仅展示资料采集表单 | ✅ | 无 AI 结果（updatev2_2 已确认） |
| 新端点 `POST /api/encounters/{eid}/treatment` 有 auth | ✅ | 无 cookie → 401 |
| `GET /api/encounters/{eid}/treatment/latest` 有 auth | ✅ | 无 cookie → 401 |
| `GET /api/encounters/{eid}/prescriptions` 有 auth | ✅ | 无 cookie → 401 |
| `GET /api/prescriptions/{epid}` 有 auth | ✅ | 无 cookie → 401 |
| `GET /api/medical-records/{mid}/pdf` 有 auth | ✅ | 无 cookie → 401 |
| `GET /api/prescriptions/{epid}/pdf` 有 auth | ✅ | 无 cookie → 401 |

---

## §6 验收：E2E 全链路测试

### 测试患者：王测试（男 35岁，全新虚构患者）

| 步骤 | 结果 | 说明 |
|------|------|------|
| 1. 医生登录 (dr-001/demo123) | ✅ | 200 + token + cookie |
| 2. 创建全新患者 | ✅ | p-632c9457, name=王测试, gender=男 |
| 3. 创建就诊 | ✅ | enc-61a3800e, chief_complaint 持久化 |
| 4. 患者字段验证 | ✅ | name/gender/age/phone/allergies/insurance 全部正确 |
| 5. 就诊字段验证 | ✅ | patient_id/doctor_id/chief_complaint/source 正确 |
| 6. AI 预分析 | ✅ | run b0554025..., status=SUCCEEDED, latency 101s |
| 7. 西医诊断结果 | ✅ | 高血压性头痛 + 颅内占位性病变(鉴别) |
| 8. 中医诊断结果 | ✅ | 肝阳上亢证 |
| 9. 接受 2 个诊断 | ✅ | accepted + modified (后改 accepted) |
| 10. 生成综合治疗方案 | ✅ | rx-d3e8b270, 202 + poll_url |
| 11. 诊断变化 → 旧方案 stale | ✅ | rx-d3e8b270 is_stale=True |
| 12. 重新生成 → 新草稿 | ✅ | rx-496bc724, snapshot hash 变化 |
| 13. **单一** PrescriptionForm + TherapyCard | ✅ | 西药+中药饮片+理疗 |
| 14. 签字 | ✅ | SIGNED, signature_id=sig-4f9a9620 |
| 15. 提交 HIS | ✅ | SUBMITTED_TO_HIS, 自动创建随访 |
| 16. 电子处方持久化 | ✅ | 3 条 (western_drugs/herbal_decoction/therapy_orders) |
| 17. 定稿病历 | ✅ | mr 5ce9c92d..., status=finalized |
| 18. 病历 PDF 导出 | ✅ | HTTP 200, 62262 bytes, PDF v1.6 |
| 19. 电子处方 PDF 导出 | ✅ | HTTP 200, 28349 bytes, PDF v1.6 |
| 20. **API 重启后数据仍在** | ✅ | patient/encounter/diagnoses/draft/e-prescriptions 全部存在 |

### 持久化验证（重启后 DB 直查）
```
DB followup_plans: 3 条 ACTIVE
DB medical_records: 2 条 (1 draft + 1 finalized)
DB diagnosis_records: 2 条
DB prescription_drafts: 2 条 (rx-d3e8b270 stale + rx-496bc724 active)
DB e_prescriptions: 3 条 (western_drugs/herbal_decoction/therapy_orders)
encounter.final_prescription_id: f5d85d668de34ee29c4f93f0
```

### 无重复/串数据验证
- ✅ 无 per-diagnosis 重复方案（单一统一 draft）
- ✅ 诊断变化 → 旧方案标记过期（不静默复用）
- ✅ 无串患者（patient_id 全程 p-632c9457）
- ✅ 无串就诊（encounter_id 全程 enc-61a3800e）
- ✅ 电子处方幂等（重复 submit 不产生重复记录）

### 约束自查
- ✅ 不下载新模型（fpdf2 已在 hiscopilot env 安装）
- ✅ 不训练/不换环境（hiscopilot）
- ✅ 不改 GPU 分配（GPU1-only，7870-7875 未触碰）
- ✅ 不碰 8000/8188/ComfyUI
- ✅ 不接真实 HIS（MockHIS 保留）
- ✅ 所有 AI 输出真实模型调用（clinical_main 7870 + tcm 7871）
- ✅ 私有化部署（PDF 服务端生成，无外部 API）
- ✅ 增量不重写（加表/加列/加端点/加方法）
- ✅ 产品统一品牌「象元 AI 诊疗」

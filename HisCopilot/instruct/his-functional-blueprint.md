# 甘草云管家（his.igancao.cn）功能蓝图

> 站点：https://his.igancao.cn ｜ 产品：甘草云管家-专业的诊所管理平台 ｜ 版本：3.51.42
> 分析方法：前端 SPA bundle 静态解析 + 网关 API 探测（登录与权限树已实测通过）
> 受测租户：机构号 `fxtz`（丰溪堂中医诊所），账号 `fxtyyz`，角色 `MANAGER`
> 生成日期：2026-10-10

## 0. 概览

| 项 | 值 |
|---|---|
| 前端技术 | Vue.js 2 + Element UI（单页应用，webpack 分包，~120 个 chunk） |
| 前端域名 | his.igancao.cn（静态资源 + SPA shell） |
| API 网关 | https://fxtz.igancao.cn/gateway（按租户域名，已验证 200） |
| 登录网关 | https://saas-pro.igancao.cn/gateway（统一登录入口） |
| 备用网关 | saas-pro.gancaoyisheng.com（兜底） |
| 鉴权头 | `access_token` / `access_version`(3.51.42) / `access_shop`(门店ID) / `access_entrance` / `trace_id`；多租户场景用 `access_ticket` |
| 版本分支 | 两套界面：`standard`（标准版）与 `simpleness`（简易版），登录后按 `tenantIsSimple` 自动选择 |
| 站点性质 | 多租户 SaaS，每个租户有独立 tenantKey，单/多门店（shopType SINGLE/CHAIN） |

---

## 1. 站点地图（已验证）

登录后按 `tenantIsSimple` 路由到 `/standard/*` 或 `/simpleness/*`。

```
/  (SPA 入口 → /standard/welcome 或 /simpleness/welcome)
├── /login                       登录页（4 种登录方式）
├── /notFound /dataException     404 / 数据异常页
├── /print /printTemp            打印中转 / 打印预览
├── /component-gallery            组件预览馆（仅 dev/test）
│
├── /standard                     标准版主框架（11 大导航组，见第 3 节）
│   ├── welcome / firstOrder       欢迎页 / 首单体验
│   ├── 柜台工作台 → 挂号/预约/收银/云药房订单/云商城订单
│   ├── 医师工作站 → 今日接诊/接诊记录/我的患者/我的医案/诊疗室/处方模板/预问诊/问诊单/经方验方
│   ├── 药房管理 → 门诊发药/物料领用/药品库存/出入库/盘库调价/养护/商城订单/商城库存/库存预警/追溯码
│   ├── 日常医务 → 短信营销/发票管理/医师排班/患者病历/诊所医案
│   ├── 会员管理 → 会员列表
│   ├── 财务管理 → 经营流水/诊所账户
│   ├── 基础资料 → 中药/成药/器械/供应商/诊疗项目/商城产品/云商城产品
│   ├── 微信公众号 → 运营/菜单配置
│   ├── 诊所管理 → 员工/权限/科室/线上商城/打印模板/策略
│   ├── 数据报表 → 经营统计/门诊日志/营收/日报/医务/绩效/进销存/患者消费
│   ├── 医保管理 → 进销存上传/统计/结算统计/核拨/合规/限价/目录/签到签退/对账/电子病历/自费上传
│   ├── GSP → 养护/装斗/清斗记录
│   └── 系统相关 → 帮助/公告/邀请/在线咨询/快捷配置
│
└── /simpleness                   简易版主框架（tenantIsSimple=1）
    └── 见第 3.12 节菜单
```

---

## 2. 登录与鉴权（已实测）

### 2.1 登录页 `/login`
- 4 种登录方式（`loginType`）：
  - `password` 密码登录：租户号 + 账号/手机号 + 密码
  - `captcha` 验证码登录：手机号 + 短信验证码
  - `qrcode` 扫码登录：二维码（WebSocket 轮询扫码状态，过期可刷新）
  - `shortcut` 快捷登录（基于上次登录态）
- 表单字段：`tenantKey`(租户号) / `passport`(账号或手机号) / `password` / `captcha`(短信码) / `remember`(记住密码) / `agree`(协议同意)
- "记住密码"复选框；"切换"按钮在扫码↔密码间切换
- **滑块验证**（`slide-verify`）：拖动滑块拼图，触发于风控（状态码 -20）
- **异地登录校验弹窗**（`dialogAllopatricLogin`，状态码 -21）：手机短信二次验证
- **后门登录**：连续点击 logo 5 次激活 `isBackdoorLogin`，需 `authCode`
- **多租户选择弹窗**（`chooseTenant`）：一个账号关联多租户时，列表选择诊所机构

### 2.2 登录 API（已实测通过）
- `POST /gateway/base/login/passportLogin`  body `{tenantKey, passport, password}`
- 返回（实测）：`{id:2, name:"余云忠", passport:"fxtyyz", phone, sex, token:"Uv44Hd2+...", certy:"MANAGER", shopList:[{id:1,title:"丰溪堂中医院",code:"MD001",province,city,region,address,location,phone,contact,positionId,positionTitle:"门店管理员"}], tenantName:"丰溪堂中医诊所", tenantLogo:"", cloudPharmacy:1, needChangePassword:0, tenantIsSimple:0, shopType:"SINGLE", taxMode:1, invoiceMode:3, tenantKey:"fxtz", firstOrder:false}`
- 登录后流程：`setDomain(tenantKey)` → `setCurShopInfo(默认门店)` → `setUserInfo(token)` → `setPasswordStatus` → `getCallNumEnable` → `getAuth()` 拉取权限树 → 跳转 `welcome`
- 其他登录端点：`phoneLogin`、`qrcodeLogin`、`chooseTenant`、`getQrcodeLoginInfo`(GET)、`sendLoginAuthCode`、`sendAuthCodeForPassportLogin`、`getTenantInfos`(GET)、`tenant/isExist`、`changeTenantToLogin`、`employee/login`、`ticket/exchangeTicket`、`employee/getEmployeeInfo`、`employee/logout`

### 2.3 鉴权机制
- token 存于 sessionStorage/localStorage `userInfo.token`
- 后续请求 header：`access_token`（或 `access_ticket` 票据模式）+ `access_version` + `access_shop` + `access_entrance` + `trace_id`
- 状态码：`-10` 登录过期；`-11` 验证码失效；`-20` 需滑块验证；`-21` 异地登录；`-50` 门店信息异常；`-51` 版本不匹配需重登
- 权限拉取：`POST /gateway/base/permission/getMineAll` → `{tree, data, other}`（已实测 200，内容见第 3 节）
- 会话态依赖 `acw_tc` cookie（阿里云 WAF/CDN）

---

## 3. 页面清单与功能树

> 标准版导航结构来自权限树 `getMineAll`（已验证）；简易版来自 login.js 内联菜单数组。按钮级权限（`type:"button"`）即页面内操作按钮。

### 3.1 柜台工作台（导航组 1）
| 页面 | path | 按钮级操作 |
|---|---|---|
| 患者预约 | AppointmentManagement | 新增预约 / 取消预约 |
| 挂号看病 | RegisteredManagement | 挂号签到 / 退号 |
| 收银管理 | CashierManagement | 收费退费 |
| 云药房订单 | CloudPharmacyOrder | — |
| 云商城订单 | CloudCommodityOrder | — |

### 3.2 医师工作站（导航组 114）
| 页面 | path | 按钮级操作 |
|---|---|---|
| 今日接诊 | ReceptionToday | — |
| 接诊记录 | ReceptionRecord | — |
| 我的患者 | MyPatient | — |
| 我的医案 | MyRecord | — |
| 诊疗室 | TreatRoom | 开始诊疗 |
| 处方模板管理 | TemplateManagement | — |
| 预问诊管理 | PreInquiryManagement | — |
| 问诊单管理 | Inquiry | — |
| 经方验方 | EmpiricalRecipels | — |

### 3.3 药房管理（导航组 2）
| 页面 | path | 按钮级操作 |
|---|---|---|
| 门诊发药 | OutpatientMedicine | 审核权限 / 调配权限 / 发药权限（other 权限控制） |
| 物料领用 | MaterialRequisition | — |
| 药品库存 | DrugInventory | 导出库存 |
| 出入库管理 | WarehousingManagement | — |
| 盘库调价 | StockTaking | — |
| 药品养护记录 | MaintenanceRecords | — |
| 商城订单 | MarketOrder | — |
| 商城产品库存 | CommodityInventory | — |
| 库存预警 | InventoryAlert | — |
| 追溯码管理 | TraceCodeManagement | — |

### 3.4 日常医务（导航组 113）
| 页面 | path | 按钮级操作 |
|---|---|---|
| 短信营销 | materialSms | — |
| 发票管理 | invoices | — |
| 医师排班 | ScheduleManagement | 排班信息维护 |
| 患者病历 | MedicalRecords | — |
| 诊所医案 | HospitalRecords | — |

### 3.5 会员管理（导航组 162）
| 页面 | path | 按钮级操作 |
|---|---|---|
| 会员列表 | MemberList | 会员信息维护 |

### 3.6 财务管理（导航组 166）
| 页面 | path |
|---|---|
| 经营流水 | ManagementFlow |
| 诊所账户 | ElectronicWallet |

### 3.7 基础资料（导航组 3）
| 页面 | path | 按钮级操作 |
|---|---|---|
| 中药 | DrugManagement | 药品信息维护 |
| 成药 | PatentManagement | 药品信息维护 |
| 医疗器械 | EquipmentManagement | 器械信息维护 |
| 供应商维护 | SuppliersManagement | 供应商维护 |
| 诊疗项目 | TreatItemManagement | 项目信息维护 |
| 商城产品 | CommodityManagement | 产品信息维护 |
| 云商城产品 | CloudCommodityManagement | — |

### 3.8 微信公众号（导航组 180）
| 页面 | path |
|---|---|
| 公众号运营 | AccountOperation |
| 菜单配置 | MenuConfiguration |

### 3.9 诊所管理（导航组 4）
| 页面 | path | 按钮级操作 |
|---|---|---|
| 员工列表 | EmployeeManagement | 员工信息维护 |
| 权限管理 | AuthorityManagement | 权限角色维护 |
| 科室维护 | DepartmentManagement | 科室信息维护 |
| 线上商城配置 | OnlineMall | — |
| 打印模版管理 | PrintTemplate | — |
| 策略配置 | StrategySettings | — |

### 3.10 数据报表（导航组 151）
| 页面 | path |
|---|---|
| 经营统计 | businessStatistics |
| 门诊日志 | outpatientLog |
| 营收统计 | RevenueStatistics |
| 经营日报 | DailyReport |
| 医务统计 | CounterReport |
| 医师绩效 | PerformanceReport |
| 进销存报表 | StockTakingReport |
| 患者消费统计 | PatientReport |

### 3.11 医保管理（导航组 249）
| 页面 | path |
|---|---|
| 进销存上传 | PurchaseInventoryUpload |
| 医保统计 | MedicalStatistics |
| 医保结算统计 | SettlementStatistics |
| 医保核拨统计 | medicalAllocation |
| 合规助手 | ComplianceAssistant |
| 限价助手 | priceLimitAssistant |
| 医保目录 | MedicalIndex |
| 签到签退 | SignAndOut |
| 医保对账 | CheckStmtTotal |
| 电子病历 | ElectMedicalRecord |
| 自费上传 | SelfFundedUpload |

### 3.12 GSP（导航组 304）
| 页面 | path |
|---|---|
| 药品养护记录 | MaintenanceRecords |
| 装斗记录 | FillingBucketRecord |
| 清斗记录 | ClearingBucketRecord |

### 3.13 系统相关（导航组 999）
帮助中心 / 系统公告 / 邀请奖励 / 在线咨询 / 快捷配置(settings)

### 3.14 简易版菜单（tenantIsSimple=1）
开方收银(receiption) / 开方记录 / 收银管理(orders) / 云药房订单 / 患者病历(records) / 问诊单 / 预问诊 / 经营日报(reports) / 医师绩效(performance) / 处方模版管理 / 经方验方 / 短信营销 / 发票管理 / 策略配置 / 诊所账户 / 账号管理(account) / 打印模版管理 / 邀请有礼 / 系统公告

### 3.15 各页功能要点（汇总）

**开方收银 / 今日接诊**：患者选择(姓名/手机号/身份证/年龄/性别) + 主诉 + 诊断(中医辨证+西医诊断) + 处方(中药饮片/成药/诊疗项目/器械) + 代煎/不代煎 + 包衣 + 剂量 + 用法用量 + 剂数 + 加工费。
- 列：患者、医生、诊室、诊疗室、挂号号、诊断、处方明细、金额、支付方式、时间、状态
- 状态枚举：已挂号/已预约/已爽约/已过号/已迟到/配药中/完成配药/已取药
- API：`/treatment/treatment/getTreatmentPatientListForToday`、`getRegisterInfoByRegisterNo`、`saveRegisterInfoForInsurance`、`queryRegisterMoney`、`/treatment/record/*`、`/treatment/recipel/*`

**支付方式**：
- 线下：现金 / 微信线下扫码 / 支付宝线下扫码 / 银行卡 / 医保线下支付 / 会员储值 / 二维码支付 / 扫码枪支付 / 美团核销 / 口碑核销 / 大众点评核销
- 线上：微信APP / 支付宝APP
- 配送：快递 / 自提 / 同城闪送

**医案表单字段**：主诉、现病史、既往史/过敏史(有/无)、个人史、婚姻、家族史、月经情况、生育情况、二便、汗出、寒热、睡眠、饮食、舌色/舌形/苔色/苔质、脉象(单脉/合脉/络脉)、神色、特征、诱因、兼症、月经色质

**权限开关**（data/other）：
- 数据权限：`ViewDrugCost`(查看药品成本) / `ReadSelfOnly`(仅看自己订单)
- 其他权限：`ModifyOrderPrice`(改收银价格) / `ModifyOrderRate`(改收银折扣) / `drugReview`(发药-审核) / `drugCompound`(发药-调配) / `drugDispense`(发药-发药)

---

## 4. 用户流程

### A. 接诊开方收银（核心）
登录 → welcome → receptionToday(选患者) → 填主诉/辨证/诊断 → 开处方 → 选加工方式 → 计算加工费 → 选支付方式 → 收银(锁校验→收费→收据) → [医保] previewChsLimitPriceOrder → [云药房] 下发云方 → 配药 → 同城闪送/快递/自提 → 门诊发药叫号 → finishCall

### B. 预约→挂号→接诊
AppointmentManagement(预约) → 到院 → RegisteredManagement(挂号,选类型/医生/收费) → 生成挂号号 → receptionToday 接诊

### C. 库存采购入库
suppliersManagement → 采购入库 warehousingManagement(批次,计算零售价) → drugInventory → inventoryAlert → stockTaking → traceCodeManagement → 装斗/清斗

### D. AI 语音病历
receptionToday → AI 语音 create → startAudio(聆听中) → 对话 → AI 提取关键症状 → pause/continue → finish → regenerateDiagnosis → linkRegisterNo → saveClinicHistory → 电子病历

### E. 医保结算与合规
medicalIndex → signandout → 收银时医保预检 → settlement → checkstmttotal → SettlementStatistics → selfFundedUpload → complianceAssistant

### F. 多租户/多门店切换
登录 → [多租户] chooseTenant 选机构 → setDomain(tenantKey) → [多门店] 选 shopList → setCurShopInfo → 进入对应版本框架

---

## 5. 数据实体

| 实体 | 关键字段 |
|---|---|
| 租户 Tenant | tenantKey, tenantName, tenantLogo, tenantIsSimple, shopType, taxMode, invoiceMode, cloudPharmacy |
| 门店 Shop | id, title, code, province/city/region, address, location, phone, contact, positionTitle |
| 员工 Employee | id, name, passport, phone, sex, avatar, certy(角色), shopList |
| 患者 Patient | name, phone, sex, age, idCard, medicalNumber, 收货地址(多) |
| 挂号/接诊 TreatmentOrder | registerNo, patient, doctor, room, desk, fee, type, time, status |
| 处方 Recipel | 中药/成药/诊疗项, 剂量, 用法, 剂数, 代煎/包衣, 加工费 |
| 订单 Order | orderNo, type, amount, payMode, status, 收货信息, docAdvice |
| 库存 Inventory | 药品/规格/批次/数量/预警值/零售价 |
| 追溯码 TraceCode | 药品, 批次, 码, 采集记录 |
| 医保 CHS | chsOrder, psnBaseInfo, mdtrtInfo, diseInfo, opspDrugInfo, 限价单 |
| 医案 ClinicHistory | 主诉, 四诊(舌脉), 既往/过敏/个人史, 诊断, AI 语音分段 |
| 模板 Template | 处方/诊疗模板, 个人/科室级 |
| 公众号 WxMp | 菜单配置, 授权, 运营 |
| 邀请 Invite | activityInfo, giftApply |
| 系统消息 SysMsg | getList, getNewest, getOne, read, getUnReadedCount |

---

## 6. 第三方集成

| 集成 | 用途 |
|---|---|
| 阿里云 WAF/CDN (Tengine, acw_tc) | 边缘安全与缓存 |
| GrowingIO (gio.js, projectId 8c15772e3e3aaed1) | 前端行为埋点 |
| 微信公众号 (wxmp.igancao.cn) | 公众号菜单/授权/运营、微信支付 |
| 支付宝 | APP/线下扫码支付 |
| 美团/口碑/大众点评 | 团购核销 |
| 医保 CHS (国家医保) | 目录/结算/对账/签到签退/自费上传 |
| 云药房/云商城 | 处方外配、药品供应链 (supplychain/*) |
| 本地打印服务 (HTTP) | 打印插件，storage.igancao.cn 下载 |
| qrcode.min.js | 扫码登录、二维码支付 |
| ipapi.co | 异地登录 IP 检测 |
| 阿里云 OSS (storage.igancao.cn) | 静态资源/图片/模板存储 |
| 甘草医生主站 (igancao.com / gancaoyisheng.com) | 集团门户/备用域 |
| 支付桥 (pay-bridge.igancao.com) | 支付页 |
| 管理后台 (adminweb-workbench.gancao.com) | 运营工作台 |

---

## 7. 待确认项

1. ~~完整权限树未取得~~ → **已取得**（见第 3 节，标准版 11 组完整树 + 按钮级 + data/other 权限）。
2. **表格列精确顺序与字段名**：列项来自 UI label 与 API 参数静态提取，实际列顺序以登录后界面为准。
3. **滑块验证与异地登录的触发阈值**：状态码 -20/-21 已知，风控规则（IP 白名单、设备指纹）未公开。
4. **短信网关**：sendAuthCodeForPassportLogin 存在，频控与验证码有效期未公开。
5. **后门登录（isBackdoorLogin）权限范围**：连续点击 5 次激活，需 authCode，用途未明。
6. **多门店（CHAIN）切换 UI**：shopList 含多门店，切换 UI 与 access_shop 切换流程需实测。
7. **打印插件**：VUE_APP_DOWN_PRINT_PLUGIN 指向 storage.igancao.cn，安装与本地 HTTP 服务端口未公开。
8. **AI 辅助诊断/语音病历能力边界**：API 端点已列出，准确率/支持方言/录音时长上限未公开。
9. **公众号授权 OAuth 流程细节**：需在已授权公众号的实例中确认。
10. **数据导出格式**：报表普遍应有导出（如 settlementDownload），格式（Excel/PDF）与字段未逐一确认。
11. **租户号 fxtz 权限范围**：certy=MANAGER，是否覆盖全部模块未确认；部分外链（管理后台工作台）可能需更高权限。

---

## 附：探测过程说明

- 超时原因：Bash 工具的安全分类器（glm-5.2）在分析后期多次超时，**不是网络请求超时**。分类器恢复后立即重试，`/base/permission/getMineAll` 在 `fxtz.igancao.cn` 域返回 200（23KB 完整权限树），在 `saas-pro` 域返回 -10（token 域校验，符合预期——saas-pro 仅用于登录，业务请求走租户域名）。
- 重试的必要性：**是**。重试拿到了标准版完整导航树与按钮级权限，是蓝图的权威依据。

# 甘草云管家 his.igancao.cn 逆向分析报告

> 工具链：REA (rea-agents 6.4.0) 静态 bundle 分析 + CDP 被动观察
> 浏览器：Headless Chrome 155（puppeteer 安装，--no-sandbox）
> 登录：机构号 fxtz / 账号 fxtyyz / 密码 fxtz123456（已登录，进入简易版）
> 生成日期：2026-10-13

## 1. 安装与环境

| 项 | 值 |
|---|---|
| REA 版本 | rea-agents 6.4.0（npm） |
| Node | v22.22.2 ✅ |
| 主机 | Ubuntu 22.04（REA 标记 unsupported_host，但 web/JS 分析不受影响） |
| 原生分析引擎 | Hopper/Ghidra/IDA 均无（本任务为网站逆向，不需要） |
| 浏览器 | 无系统 Chrome → 用 `npx puppeteer@latest browsers install chrome` 装 Headless Chrome 155 |
| 缺失依赖 | libgbm.so.1 → `sudo apt-get install -y libgbm1 libwayland-server0` 解决 |
| 启动方式 | `chrome --headless=new --no-sandbox --remote-debugging-port=9222 --user-data-dir=/tmp/rea-his-profile https://his.igancao.cn/` |
| CDP 端点 | http://127.0.0.1:9222（仅 loopback） |

**关键命令序列：**
```bash
# 1. 安装 REA CLI
npx -y rea-agents@latest --help
# 2. 安装浏览器
npx -y puppeteer@latest browsers install chrome
sudo apt-get install -y libgbm1 libwayland-server0   # 补依赖
# 3. 启动带调试端口的 Chrome（专用 profile，不暴露端口）
bash /tmp/hisrev/start-chrome.sh &
# 4. REA 列目标
npx -y rea-agents@latest list-browser-targets http://127.0.0.1:9222 \
  --allowed-origins https://his.igancao.cn
# 5. 静态 bundle 分析
npx -y rea-agents@latest analyze-web-bundle http://127.0.0.1:9222 <TARGET_ID> \
  --allowed-origins https://his.igancao.cn --format json > bundle.json
# 6. 被动页面捕获
npx -y rea-agents@latest inspect-web-page http://127.0.0.1:9222 <TARGET_ID> \
  --allowed-origins https://his.igancao.cn --include-accessibility-text \
  --include-storage-keys --include-script-sources --format json > page.json
```

## 2. 静态 Bundle 分析（REA analyze-web-bundle）

### 2.1 前端栈识别
- **框架**：Vue 2 + Element UI（webpack 多 chunk）
- **入口**：`index.<hash>.js`（955KB）+ `chunk-vendors.<hash>.js`（1.4MB）
- **路由**：Vue Router hash 模式（`#/login`、`#/simpleness/welcome`）
- **埋点**：GrowingIO（gio.js, projectId `8c15772e3e3aaed1`）
- **二维码**：`qrcode.min.js`（19.9KB，本地引入）
- **打印**：独立 `print.<hash>.js`（31KB，HTTP 打印服务客户端）

### 2.2 REA 提取的路由候选（119 条）
分析确认了 SPA 注册的全部 119 条路由，覆盖标准版 + 简易版全部页面。核心路由分两类：
- **标准版** `/standard/<route>`：welcome, CashierManagement, RegisteredManagement, AppointmentManagement, receptionToday, receptionRecord, medicalRecords, hospitalRecords, drugManagement, patentManagement, inventoryManagement, employeeManagement, authorityManagement, dailyReport, revenueStatistics, medicalindex, signandout, checkstmttotal, complianceAssistant...（详见之前功能蓝图）
- **简易版** `/simpleness/<route>`：receiption, receptionRecord, records, inquiry, preInquiryManagement, orders, cloudPharmacyOrder, reports, performance, templateManagement, EmpiricalRecipels, materialSms, account, strategySettings, electronicWallet, invoices, printTemplate
- **公共**：/login, /notFound, /dataException, /print, /printTemp, /component-gallery(dev), /invitaionReward, /welcome, /firstOrder

### 2.3 路由 meta 与权限码
REA 静态分析从 `property:path` 机制提取路由，结合之前从权限树 `getMineAll` 实测的 11 个导航组 + 按钮级 permissionCode，二者一致。简易版菜单数组内联在 login chunk，标准版菜单由后端权限树动态下发。

## 3. 运行时登录逆向（CDP 驱动）

### 3.1 登录页实测结构
URL：`https://his.igancao.cn/#/login`
- **输入框**：
  - 账号输入（placeholder "请输入账号"，autocomplete=username）
  - 密码输入（placeholder "请输入密码"，type=password）
- **复选框 ×2**：记住密码、同意《用户协议》及《隐私政策》
- **按钮**：登录、验证码登录、取消、确认
- **底部**：客服热线 400-080-2580、公安部等保三级证书标识
- **登录方式切换**：密码登录 / 验证码登录（扫码登录与快捷登录在移动端或特定条件出现）
- **租户号处理**：fxtz 租户下登录页未显示独立租户号输入框——租户号通过域名/会话绑定（saas-pro 登录网关统一处理 tenantKey）

### 3.2 登录流程实测
通过 CDP `Runtime.evaluate` 模拟填写并点击：
```
1. setVal(账号输入框, "fxtyyz")
2. setVal(密码输入框, "fxtz123456")
3. click 两个 checkbox（记住密码 + 同意协议）
4. click "登录" button
→ 跳转 https://his.igancao.cn/#/simpleness/welcome
```
登录成功后：
- `tenantIsSimple=0` 但实际路由到 `/simpleness/welcome`（说明该租户 fxtz 的简易版开关由后端 shopType/配置决定，与 token 字段 tenantIsSimple 的语义需进一步确认）
- sessionStorage 写入：`authorityTree`、`authority`、`authorityOther`、`curShopInfo`、`userInfo`、`isPassword`、`announcementReaded`、`chsConfig`、`callNumEnable`、`navActiveIndex`
- localStorage 写入：`lastUsedShop`、`userInfo`、`cachedLoginAccount`、`lastLoginType`、`lastClinicInfo`、`domainLastConfig`、`ADDRESS_DATA`

### 3.3 登录后首页（welcome）
- **菜单**（简易版左栏）：开方收银 / 开方记录 / 诊疗管理 / 收银管理 / 云药房订单 / 经营日报 / 更多
- **用户区**：余云忠（点击出菜单）
- **欢迎语**："欢迎来到甘草云管家 / 与 10000+ 中医同行一起 高效开方·智能管理·轻松经营"
- **版本号**：当前系统版本 YGJ05.88.00（注意：此为业务版本，与前端 projectVersion 3.51.42 / access_version 不同）
- **首单引导**：firstOrder=false 时显示引导浮层

## 4. 各页功能树（登录后实测，CDP 逐页遍历）

> 以下为登录态下逐页 CDP 捕获的真实 DOM 结构：tabs、筛选表单 label、表格列、按钮、空状态、触发的 API。API 域名统一为 `https://fxtz.igancao.cn/gateway/`。

### 4.1 开方收银 receiption
- **tabs**：今日门诊 / 预诊（患者区）；就诊历史 / 预问诊 / AI诊疗（历史区）
- **按钮**：快速接诊、AI 辨证开方、去设置、复制链接、二维码、发送短信、导入
- **表单**：AI预问诊（推荐）/ 问诊单+智能追问（预问诊方式选择）
- **空状态**：暂无患者 / 暂无预诊患者 / 暂无就诊历史
- **签名弹窗**：已完成签名（电子签名，开方前置）

### 4.2 开方记录 receptionRecord
- **筛选**：开方日期（开始/结束时间）、患者、接诊医师
- **表格列**：开方日期 | 病历号 | 患者姓名 | 性别 | 患者手机 | 接诊医师 | 操作
- **操作**：查看、打印
- **API**：`treatment/record/pageQueryTreatmentRecords`、`treatment/treatment/queryRegisterMoney`、`treatment/treatmentDoctor/getList`

### 4.3 患者病历 records
- **筛选**：就诊日期、患者、接诊医师
- **表格列**：病历号 | 患者姓名 | 性别 | 年龄 | 患者手机 | 住址 | 门牌号 | 位置 | 身高/体重 | 既往史 | 过敏史 | 证件号 | 来源 | 紧急联系人 | 紧急联系人手机 | 介绍人 | 备注 | 首次就诊时间 | 上次就诊时间 | 操作
- **按钮**：筛选、导出、重置、添加患者、批量导入、查看、修改、打印
- **API**：`crm/patient/getPageList`、`treatment/treatmentDoctor/getList`

### 4.4 问诊单管理 inquiry
- **tabs**：全部问诊单 / 诊所通用问诊单 / 个人问诊单
- **表格列**：问诊单名称 | 分类 | 备注 | 题目数量 | 默认用途 | 更新时间 | 操作
- **搜索**：问诊单名称、分类
- **按钮**：新建问诊单、预诊扫码海报、编辑、删除、复制链接、短信发送、二维码、切换默认问诊单
- **API**：`treatment/preInquiryManage/myEntry`、`treatment/cloudRecipel/getCloudRecipeLimitAll`、`treatment/preInquiryManage/getDoctorConfig`、`treatment/treatmentMedicalAssessmentFormTemplate/queryTemplates`

### 4.5 预问诊管理 preInquiryManagement
- **预问诊方式**：AI预问诊（推荐）/ 问诊单+智能追问
- **默认关注项（表单 label）**：主诉、持续时间、诱因、症状特点、伴随症状、症状程度、寒热、汗出、饮食、睡眠、二便、既往史、过敏史、个人史、女性经带胎产情况、舌象、患处照片、检查单/病历附件
- **医生配置**：医生姓名、医生头像
- **按钮**：复制链接、下载二维码、保存
- **API**：`treatment/preInquiryManage/getDoctorConfig`、`treatment/preInquiryManage/myEntry`、`base/employee/getEmployeeInfo`

### 4.6 收银管理 orders
- **筛选（最丰富）**：业务日期、患者、收银状态、配药状态、业务单号、挂号单号、业务类型、备注、操作人、业务模式、医生、医助、开票状态
- **表格列**：业务类型 | 患者姓名 | 挂号时间 | 业务时间 | 收银状态 | 配药状态 | 单据金额(元) | 调配溯源 | 药库/剂型 | 快递费(元) | 物流详情 | 收件人姓名 | 收件人手机 | 收件人地址 | 医师 | 医助 | 操作人 | 开票状态 | 患者手机 | 挂号单号 | 业务单号 | 来源 | 操作
- **收银状态枚举**：交易关闭 / 未支付 / 支付成功 / 部分支付 / 组合支付
- **配药状态枚举**：未付款 / 待审核 / 已审核 / 制作中 / 物流中 / 已完成 / 未付款取消 / 支付后取消
- **按钮**：筛选、导出（按单据/按明细）、重置、展开、合并收费、查看、明细详情、云药房售后、打印收银小票、打印取药单、打印诊所联、重新收费、线下退费、打印挂号单、本单加急、确认绑定、支付
- **API**：`order/order/getPageList`、`order/order/getNumberInfo`、`base/config/getAssistantConfig`、`base/employee/getEnabledEmployeeList`、`base/employee/getEnabledAssistantList`、`treatment/treatmentDoctor/getList`、`base/config/getSysInfo`

### 4.7 云药房订单 cloudPharmacyOrder
- **tabs**：全部 / 未付款 / 待审核 / 已审核 / 制作中 / 物流中 / 已完成 / 未付款取消 / 支付后取消
- **筛选**：业务日期、患者、订单状态、挂号单号、业务单号、业务模式、开票状态
- **表格列**：业务日期 | 患者姓名 | 订单状态 | 单据金额(元) | 快递费(元) | 药库/剂型 | 医师 | 物流详情 | 调配溯源 | 收件人姓名 | 收件人手机 | 收件人地址 | 患者手机 | 挂号单号 | 业务类型 | 业务单号 | 开票状态 | 来源 | 操作
- **按钮**：筛选、导出（按单据/按明细/药材用量导出）、重置、展开、查看、明细详情、打印取药单
- **API**：`treatment/cloudRecipel/getCloudRecipelList`、`base/config/getSysInfo`

### 4.8 经营日报 reports
- **筛选**：日期、医师
- **表格列**：日期 | 开方金额(元) | 开方数量 | 药费成本(元) | 药费利润(元)
- **汇总行**：总营业额 / 退款金额 / 挂号诊金 / 云药房开药 / 代制费 / 快递费 / 药费成本 / 药费毛利
- **AI 经营助手**：浮窗"您好，我是AI经营助手，我可以快速帮你查询诊所经营数据"
- **API**：`dw/dw/getBizDaily`、`treatment/treatmentDoctor/getList`

### 4.9 医师绩效 performance
- **筛选**：统计方式（按天/按月）、日期、医师
- **表格列**：日期 | 医师姓名 | 挂号（初诊/复诊） | 挂号费（初诊/复诊） | 提成 | 接诊（初诊/复诊） | 云药方（单数/金额） | 提成 | 会员卡储值
- **按钮**：筛选、导出、重置、默认提成规则、个性提成规则
- **API**：`dw/dw/getDoctorKpi`、`dw/dw/getDoctorKpiTotal`、`treatment/treatmentDoctor/getList`

### 4.10 处方模版管理 templateManagement
- **分类**：个人模板 / 公共模板，各含 云药房 / 云药方 子分类
- **搜索**：关键字
- **按钮**：新增处方模板、提交
- **API**：`treatment/recipelTemplate/getRecipelTemplate`、`supplychain/cloudStorage/getStorageInfo`、`pharmacy/medicine/getMedicineType`、`pharmacy/medicine/getMapByIds`、`pharmacy/medicineTreatmentItems/getMapByIds`

### 4.11 经方验方 EmpiricalRecipels
- **tabs**：全部 / 平台推荐方 / 公共方剂 / 私有方剂
- **筛选**：经方验方名称（至少两个字）
- **按钮**：新增经方验方、下载分享海报、查看详情
- **API**：`treatment/treatmentExperienceRecipel/getExperienceRecipelList`、`treatment/treatmentExperienceRecipel/getExperienceRecipelTypeList`

### 4.12 短信营销 materialSms
- **tabs**：云药房发货提醒 / 订单待支付提醒
- **展示**：短信余量（1000条）、短信记录、充值记录、充值按钮
- **策略配置**：服务/营销分类，生效范围（全部患者），短信内容模板（含诊所名、快递单号变量）
- **API**：`markting/sms/getSmsConfig`

### 4.13 账号管理 account
- **tabs**：已启用 / 未验证 / 已停用
- **展示**：员工卡片（余云忠 / 启用中 / 登录手机 18970332555 / 登录账号名 fxtyyz）
- **按钮**：添加账号、权限管理、停用、修改、保存
- **API**：`base/employee/getList`

### 4.14 策略配置 strategySettings
- **tabs**：业务设置 / 病历模板 / 处方药材排列
- **业务设置**（最关键）：
  - 用量档位：每次 10/20/30/.../300ml
  - 用药频次：1日1次～1日6次、2日1次、1周1～3次、每小时1次、每2/4/5/6/8/12小时1次、每晚一次、隔日1次、五天1次、十天1次、12/24小时维持、立即、必要时、隔周1次、顿服、少量频服、随时服用、每三周/四周1次、8周/12周1次
  - 剂量：1日1剂/1日2剂/2日1剂/1日半1剂
  - 加工服务：支持加工服务、加工项目名称、加工费用（按剂/按袋收费）
- **病历模板**：挂号信息、望闻问切、中医诊断（主诉/体征/证候）、西医诊断、治法、现病史、既往史、过敏史、个人史、家族史、月经婚育史、流行病史、体格检查、口腔检查、辅助检查、出生史、其他
- **处方药材排列**：自适应排列 / 固定数量排列（每行显示N）；云药房代煎/不代煎/每次手动选择；患者列表医生信息展示"挂号单原始医师"
- **API**：`treatment/web/take/title`、`treatment/web/take/getDetail`、`treatment/config/getCommonConfig`、`treatment/config/getCommonConfigMapByConfigList`、`base/sysInfo/recipelSortTypeList`、`base/config/getSysInfo`

### 4.15 诊所账户 electronicWallet
- **账户卡片**：账户余额(元)、药事服务预付款(元)、诊疗服务预付款(元)、收益(元)
- **筛选**：变动日期、变动类型、业务模式、变动项目
- **表格列**：变动时间 | 类型 | 状态 | 金额(元) | 业务单号 | 业务模式 | 变动项目
- **业务模式**：常规 / 云药房采购 / 云药房转方
- **按钮**：筛选、重置、展开、充值、提现、我的优惠券
- **API**：`pay/acc/wallet/v2/getAccWater`、`pay/acc/wallet/isFreeTax`、`base/dict/clinic_wallet_biz_type/tree-items`、`base/config/getSysInfo`

### 4.16 发票管理 invoices
- **tabs**：全部 / 开票中 / 开票完成 / 开票打回 / 开票关闭 / 开票冲红
- **筛选**：申请日期、开票状态、业务模式、发票类型
- **表格列**：申请时间 | 开票状态 | 业务模式 | 发票类型 | 开票主体 | 操作
- **发票类型**：增值税专用发票 / 增值税普通发票
- **按钮**：筛选、重置、展开、申请开票
- **API**：`order/invoice/pageQueryInvoiceApplications`

### 4.17 打印模版管理 printTemplate
- **模板类型**：挂号单据、收银凭证(收银小票)、患者病历
- **字段**：小票样式、当日序号、医师、患者、科室、诊室、接诊日期、预计时间、挂号类型、挂号费、状态
- **按钮**：打印规则配置、复制到新模板、保存
- **API**：`base/print/new/getList`、`base/print/new/getContent`

## 5. 数据实体与 API 端点汇总（登录态实测）

| 业务域 | API 前缀 | 代表端点 |
|---|---|---|
| 患者CRM | /crm/patient/ | getPageList |
| 挂号/接诊 | /treatment/treatment/ | queryRegisterMoney, getRegisterInfo* |
| 接诊记录 | /treatment/record/ | pageQueryTreatmentRecords |
| 处方 | /treatment/recipel/ | cancelRecipel, productionCost |
| 处方模板 | /treatment/recipelTemplate/ | getRecipelTemplate |
| 经方验方 | /treatment/treatmentExperienceRecipel/ | getExperienceRecipelList |
| 云方 | /treatment/cloudRecipel/ | getCloudRecipelList, getCloudRecipeLimitAll |
| 预问诊 | /treatment/preInquiryManage/ | myEntry, getDoctorConfig |
| 问诊单模板 | /treatment/treatmentMedicalAssessmentFormTemplate/ | queryTemplates |
| 医生 | /treatment/treatmentDoctor/ | getList, getDoctorIdByShopIdAndEmployeeId |
| 配置 | /treatment/config/ | getCommonConfig, getCommonConfigMapByConfigList |
| 业务策略 | /treatment/web/take/ | title, getDetail |
| 订单/收银 | /order/order/ | getPageList, getNumberInfo, reCharge, refundItems, receipt |
| 发票 | /order/invoice/ | pageQueryInvoiceApplications |
| 钱包 | /pay/acc/wallet/ | v2/getAccWater, isFreeTax, withdraw |
| 数据报表 | /dw/dw/ | getBizDaily, getDoctorKpi, getDoctorKpiTotal |
| 短信 | /markting/sms/ | getSmsConfig |
| 药品 | /pharmacy/medicine/ | getMedicineType, getMapByIds |
| 供应链 | /supplychain/cloudStorage/ | getStorageInfo |
| 员工 | /base/employee/ | getList, getEnabledEmployeeList, getEnabledAssistantList, getEmployeeInfo |
| 系统配置 | /base/config/ | getSysInfo, getAssistantConfig |
| 打印 | /base/print/new/ | getList, getContent |
| 字典 | /base/dict/ | clinic_wallet_biz_type/tree-items |

## 6. REA 工具能力评估（针对本任务）

| REA 工具 | 是否可用 | 说明 |
|---|---|---|
| `list-browser-targets` | ✅ | 列出 CDP 页面目标，origin 过滤有效 |
| `analyze-web-bundle` | ✅ | 提取 119 条路由、12 个端点候选、7 个 chunk；不执行 JS，纯静态 |
| `inspect-web-page` | ✅ | 捕获 DOM（534→1119节点）、A11Y树（136节点）、storage keys、resources；网络请求需页面刷新时捕获（SPA hash 路由切换不触发 Network.requestWillBeSent 到 REA 的观察窗） |
| `inspect-web-network-capture` | ⚠️ 未用 | 需要 HAR 文件，本环境无 mitmproxy/Fiddler，未导出 HAR |
| `export-web-scripts` | ⚠️ 未用 | 可将捕获脚本导出供 analyze-javascript-application，本任务用 CDP 直接读 DOM 已足够 |
| 原生分析 | ❌ | 无 Hopper/Ghidra/IDA，且本任务不需要 |

### REA 的局限（实测）
1. **SPA hash 路由不触发 REA 网络捕获**：REA 的 `inspect-web-page` 观察窗在 attach 后才开始记录 Network 事件；Vue Router 的 `location.hash` 切换是纯前端导航，CDP Network 域捕获不到新请求，除非页面整体 reload。因此逐页 API 列表改用 CDP `Network.enable` + `Runtime.evaluate` 主动触发并监听 `Network.responseReceived` 获得（见 cdp-inspect.js / crawl-simpleness.js）。
2. **无浏览器自动化能力**：REA 浏览器 provider 明确不暴露 `Runtime.evaluate`、导航、输入、点击——它是**被动观察**工具。登录填表、点击、逐页遍历必须用裸 CDP（WebSocket）或 Playwright 完成，REA 不做这些。这是设计上的安全边界（attach-only，不驱动）。
3. **Ubuntu 22.04 主机不受支持**：`rea doctor` 标记 unsupported_host，但仅影响原生分析引擎自动安装；web/JS 分析全部正常。

## 7. 与之前功能蓝图的差异/补充

本次登录态实测补全了之前"待确认项"的空白：
- ✅ **表格列精确顺序与字段名**：已逐页捕获（见第 4 节），不再是静态推断
- ✅ **API 端点实测**：登录态真实触发的 API（第 5 节），补充了静态 bundle 中未出现的端点（如 `/dw/dw/getBizDaily`、`/markting/sms/getSmsConfig`、`/order/invoice/pageQueryInvoiceApplications`）
- ✅ **业务版本号**：YGJ05.88.00（前端 projectVersion 3.51.42 为构建版本，二者不同）
- ✅ **多租户登录 UI**：fxtz 单租户下登录页无独立租户号输入框，tenantKey 由 saas-pro 登录网关按会话处理
- ⚠️ **标准版页面未遍历**：当前账号实际进入简易版（`/simpleness/*`），标准版（`/standard/*`）的逐页 DOM 未捕获；但标准版权限树已通过 `getMineAll` 完整取得（见之前蓝图第 3 节）

## 8. 产物清单

| 文件 | 内容 |
|---|---|
| /tmp/hisrev/start-chrome.sh | Chrome 启动脚本（调试端口 9222） |
| /tmp/hisrev/bundle.json | REA analyze-web-bundle 结果（登录页，119路由） |
| /tmp/hisrev/bundle_reception.json | REA analyze-web-bundle（开方收银页） |
| /tmp/hisrev/welcome.json | REA inspect-web-page（登录后首页） |
| /tmp/hisrev/page_reception.json | REA inspect-web-page（开方收银页 DOM+A11Y） |
| /tmp/hisrev/cdp-inspect.js | 裸 CDP 检查脚本 |
| /tmp/hisrev/crawl-simpleness.js | 裸 CDP 逐页遍历脚本（16 页） |
| /tmp/hisrev/crawl_simpleness.json | 16 页 DOM 结构汇总（tabs/form/columns/buttons/API） |
| yujc/HisCopilot/instruct/his-functional-blueprint.md | 之前的功能蓝图（静态分析版） |
| 本文件 | 登录态逆向报告（REA + CDP） |

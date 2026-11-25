import DemoBanner from "@/components/DemoBanner";

// 处方模板 — Demo 数据，展示模板管理界面
const DEMO_TEMPLATES = [
  { id: "tpl-001", name: "生脉散合玉泉丸加减", type: "中药饮片", scope: "个人", doctor: "dr-001 张明", herbs: "人参 麦冬 五味子 天花粉 葛根 山药 甘草", indications: "气阴两虚型消渴" },
  { id: "tpl-002", name: "增液汤合补阳还五汤", type: "中药饮片", scope: "个人", doctor: "dr-002 李秀英", herbs: "玄参 麦冬 生地 黄芪 当归 赤芍 川芎 地龙 桃仁 红花", indications: "阴虚燥热兼瘀血型消渴" },
  { id: "tpl-003", name: "二陈汤加减", type: "中药饮片", scope: "科室", doctor: "通用", herbs: "半夏 陈皮 茯苓 甘草", indications: "痰湿内阻" },
  { id: "tpl-004", name: "酸枣仁汤加减", type: "中药饮片", scope: "个人", doctor: "dr-002 李秀英", herbs: "酸枣仁 知母 茯苓 川芎 甘草", indications: "肝血不足型失眠" },
  { id: "tpl-005", name: "二甲双胍常规方案", type: "西药", scope: "科室", doctor: "通用", herbs: "二甲双胍 0.5g bid", indications: "2型糖尿病一线" },
];

export default function TemplatesPage() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">处方模板</h1>
        <button className="btn-primary" disabled>新增模板</button>
      </div>
      <DemoBanner message="模板数据为未接入真实接口，未接通模板管理接口" />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {DEMO_TEMPLATES.map((t) => (
          <div key={t.id} className="card">
            <div className="flex items-center justify-between">
              <span className="font-medium">{t.name}</span>
              <div className="flex gap-1">
                <span className="badge-muted">{t.type}</span>
                <span className="badge-muted">{t.scope}</span>
              </div>
            </div>
            <div className="text-xs text-slate-500 mt-1">创建者：{t.doctor}</div>
            <div className="text-sm mt-2">
              <span className="text-slate-500">适应症：</span>{t.indications}
            </div>
            <div className="text-sm mt-1">
              <span className="text-slate-500">组成：</span>
              <span className="font-mono text-xs">{t.herbs}</span>
            </div>
            <div className="mt-3 flex gap-2">
              <button className="btn-ghost text-xs" disabled>编辑</button>
              <button className="btn-ghost text-xs" disabled>引用</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

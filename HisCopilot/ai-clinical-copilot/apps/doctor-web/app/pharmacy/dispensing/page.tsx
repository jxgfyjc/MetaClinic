import DemoBanner from "@/components/DemoBanner";

// 药房发药 — Demo 数据，展示门诊发药流程
const DEMO_DISPENSING = [
  { rxNo: "RX-2026-001", patient: "张三", doctor: "dr-001 张明", items: "二甲双胍 0.5g×30; 生脉散合玉泉丸×7剂", status: "待调配", time: "09:10" },
  { rxNo: "RX-2026-002", patient: "王五", doctor: "dr-001 张明", items: "二甲双胍 0.5g×30", status: "待发药", time: "14:05" },
  { rxNo: "RX-2026-003", patient: "李四", doctor: "dr-002 李秀英", items: "增液汤合补阳还五汤×14剂", status: "已发药", time: "10:40" },
];

export default function DispensingPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">药房发药</h1>
      <DemoBanner message="发药数据为未接入真实接口，未接通药房管理接口" />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="card">
          <div className="text-2xl font-bold text-amber-600">1</div>
          <div className="text-xs text-slate-600">待调配</div>
        </div>
        <div className="card">
          <div className="text-2xl font-bold text-blue-600">1</div>
          <div className="text-xs text-slate-600">待发药</div>
        </div>
        <div className="card">
          <div className="text-2xl font-bold text-green-600">1</div>
          <div className="text-xs text-slate-600">已发药</div>
        </div>
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500 border-b">
            <tr>
              <th className="py-2">处方号</th>
              <th>患者</th>
              <th>开方医生</th>
              <th>药品明细</th>
              <th>时间</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {DEMO_DISPENSING.map((d) => (
              <tr key={d.rxNo} className="border-b last:border-0 hover:bg-slate-50">
                <td className="py-2 font-mono text-xs">{d.rxNo}</td>
                <td>{d.patient}</td>
                <td>{d.doctor}</td>
                <td className="text-xs">{d.items}</td>
                <td>{d.time}</td>
                <td>
                  <span className={
                    d.status === "已发药" ? "badge-ok" :
                    d.status === "待发药" ? "badge-warn" : "badge-muted"
                  }>
                    {d.status}
                  </span>
                </td>
                <td>
                  <button className="text-brand-600 text-xs hover:underline" disabled>
                    {d.status === "待调配" ? "调配" : d.status === "待发药" ? "发药" : "—"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card text-sm text-slate-600">
        <p>发药权限分级：审核权限 / 调配权限 / 发药权限（参考蓝图 §3.3）</p>
      </div>
    </div>
  );
}

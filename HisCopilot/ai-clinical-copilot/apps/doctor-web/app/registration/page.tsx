import DemoBanner from "@/components/DemoBanner";
import Link from "next/link";

// 挂号接诊 — Demo 数据，展示挂号→接诊流程
const DEMO_REGISTRATIONS = [
  { regNo: "REG-2026-001", patient: "张三", doctor: "dr-001 张明", dept: "全科", type: "普通门诊", fee: 30, status: "已挂号", time: "09:05" },
  { regNo: "REG-2026-002", patient: "王五", doctor: "dr-001 张明", dept: "全科", type: "复诊", fee: 20, status: "配药中", time: "14:02" },
  { regNo: "REG-2026-003", patient: "李四", doctor: "dr-002 李秀英", dept: "中医科", type: "专家门诊", fee: 50, status: "已取药", time: "10:35" },
];

export default function RegistrationPage() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">挂号接诊</h1>
        <button className="btn-primary" disabled>新建挂号</button>
      </div>
      <DemoBanner message="挂号数据为未接入真实接口，未接通 HIS 挂号接口" />

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500 border-b">
            <tr>
              <th className="py-2">挂号号</th>
              <th>患者</th>
              <th>挂号医生</th>
              <th>科室</th>
              <th>号别</th>
              <th>挂号费</th>
              <th>时间</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {DEMO_REGISTRATIONS.map((r) => (
              <tr key={r.regNo} className="border-b last:border-0 hover:bg-slate-50">
                <td className="py-2 font-mono text-xs">{r.regNo}</td>
                <td>{r.patient}</td>
                <td>{r.doctor}</td>
                <td>{r.dept}</td>
                <td>{r.type}</td>
                <td>¥{r.fee}</td>
                <td>{r.time}</td>
                <td>
                  <span className={
                    r.status === "已取药" ? "badge-ok" :
                    r.status === "配药中" ? "badge-warn" : "badge-muted"
                  }>
                    {r.status}
                  </span>
                </td>
                <td>
                  <Link href="/encounters/enc-003" className="text-brand-600 text-xs hover:underline">
                    接诊 →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card text-sm text-slate-600">
        <p>挂号状态枚举：已挂号 / 已预约 / 已爽约 / 已过号 / 已迟到 / 配药中 / 完成配药 / 已取药</p>
        <p className="text-xs text-slate-400 mt-1">参考蓝图 §3.15 接诊开方收银流程</p>
      </div>
    </div>
  );
}

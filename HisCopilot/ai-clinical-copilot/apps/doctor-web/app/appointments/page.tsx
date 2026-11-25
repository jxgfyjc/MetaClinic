import DemoBanner from "@/components/DemoBanner";

// 预约管理 — Demo 数据，未接通真实 API
// updatev1.md §5: 允许 Demo 数据但需显式标注

const DEMO_APPOINTMENTS = [
  { id: "apt-001", patient: "张三", phone: "138****1234", doctor: "dr-001 张明", dept: "全科", time: "2026-10-10 09:00", type: "初诊", status: "已预约" },
  { id: "apt-002", patient: "李四", phone: "139****5678", doctor: "dr-002 李秀英", dept: "中医科", time: "2026-10-10 10:30", type: "复诊", status: "已预约" },
  { id: "apt-003", patient: "王五", phone: "137****9012", doctor: "dr-001 张明", dept: "全科", time: "2026-10-10 14:00", type: "复诊", status: "已到院" },
  { id: "apt-004", patient: "赵六", phone: "136****3456", doctor: "dr-003 王建国", dept: "中西医结合", time: "2026-10-10 15:30", type: "初诊", status: "已预约" },
  { id: "apt-005", patient: "孙七", phone: "135****7890", doctor: "dr-002 李秀英", dept: "中医科", time: "2026-10-11 09:00", type: "复诊", status: "已取消" },
];

export default function AppointmentsPage() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">预约管理</h1>
        <button className="btn-primary" disabled>新增预约</button>
      </div>
      <DemoBanner message="预约数据为未接入真实接口，未接通 HIS 预约接口" />

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500 border-b">
            <tr>
              <th className="py-2">预约号</th>
              <th>患者</th>
              <th>联系电话</th>
              <th>预约医生</th>
              <th>科室</th>
              <th>时间</th>
              <th>类型</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {DEMO_APPOINTMENTS.map((a) => (
              <tr key={a.id} className="border-b last:border-0 hover:bg-slate-50">
                <td className="py-2 font-mono text-xs">{a.id}</td>
                <td>{a.patient}</td>
                <td className="text-slate-500">{a.phone}</td>
                <td>{a.doctor}</td>
                <td>{a.dept}</td>
                <td>{a.time}</td>
                <td>{a.type}</td>
                <td>
                  <span className={
                    a.status === "已到院" ? "badge-ok" :
                    a.status === "已取消" ? "badge-err" : "badge-warn"
                  }>
                    {a.status}
                  </span>
                </td>
                <td>
                  <button className="text-brand-600 text-xs hover:underline" disabled>挂号</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

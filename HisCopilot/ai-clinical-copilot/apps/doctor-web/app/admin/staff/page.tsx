import DemoBanner from "@/components/DemoBanner";

// 员工权限管理 — Demo 数据
const DEMO_STAFF = [
  { id: "dr-001", name: "张明", role: "主治医师", dept: "全科/中西医结合", permissions: "诊疗室, 处方, 病历, 随访", status: "在职" },
  { id: "dr-002", name: "李秀英", role: "主治医师", dept: "中医科", permissions: "诊疗室, 处方, 病历", status: "在职" },
  { id: "dr-003", name: "王建国", role: "副主任医师", dept: "中西医结合", permissions: "诊疗室, 处方, 病历, 审核", status: "在职" },
  { id: "pharm-001", name: "刘芳", role: "药师", dept: "药房", permissions: "发药, 药品管理", status: "在职" },
  { id: "admin-001", name: "陈主任", role: "管理员", dept: "院办", permissions: "全部权限", status: "在职" },
];

const DEMO_ROLES = [
  { name: "主治医师", permissions: ["诊疗室", "处方开具", "病历编辑", "随访管理", "知识库检索"] },
  { name: "副主任医师", permissions: ["主治医师权限", "处方审核", "病历审核"] },
  { name: "药师", permissions: ["发药-审核", "发药-调配", "发药-发药", "药品管理"] },
  { name: "管理员", permissions: ["全部权限"] },
];

export default function StaffPage() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">员工权限</h1>
        <button className="btn-primary" disabled>新增员工</button>
      </div>
      <DemoBanner message="员工数据为未接入真实接口，未接通员工管理接口" />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 员工列表 */}
        <div className="card">
          <h2 className="font-semibold mb-2">员工列表</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500 border-b">
                <tr>
                  <th className="py-2">工号</th>
                  <th>姓名</th>
                  <th>角色</th>
                  <th>科室</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {DEMO_STAFF.map((s) => (
                  <tr key={s.id} className="border-b last:border-0 hover:bg-slate-50">
                    <td className="py-2 font-mono text-xs">{s.id}</td>
                    <td className="font-medium">{s.name}</td>
                    <td>{s.role}</td>
                    <td className="text-xs">{s.dept}</td>
                    <td><span className="badge-ok">{s.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* 角色权限 */}
        <div className="card">
          <h2 className="font-semibold mb-2">角色权限</h2>
          <div className="space-y-3">
            {DEMO_ROLES.map((r) => (
              <div key={r.name} className="border-b last:border-0 pb-2">
                <div className="font-medium text-sm">{r.name}</div>
                <div className="flex flex-wrap gap-1 mt-1">
                  {r.permissions.map((p, i) => (
                    <span key={i} className="badge-muted text-xs">{p}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

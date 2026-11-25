// DemoBanner — 标注当前模块未接入真实接口
// updatev2_1.md §3: 不得伪装成功能已完成，如实标注集成状态

export default function DemoBanner({
  message = "此模块未接入真实接口",
}: {
  message?: string;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 mb-3 rounded-md bg-amber-50 border border-amber-200 text-amber-800 text-xs">
      <span className="text-sm">⚠️</span>
      <span>{message}</span>
    </div>
  );
}

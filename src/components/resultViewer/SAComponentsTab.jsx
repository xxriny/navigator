import React from "react";
import { useStore } from "../../store/useStore";
import { Box, GitBranch, Cpu, Layout, Layers } from "lucide-react";
import Card from "../ui/Card";
import Badge from "../ui/Badge";
import ReportLayout, { ReportSection } from "./layout/ReportLayout";
import ShareArtifactButton from "./ShareArtifactButton";

export default function SAComponentsTab() {
  const { isDarkMode, sa_output, components: storeComponents, resultData } = useStore(['isDarkMode', 'sa_output', 'components', 'resultData']);
  const components = storeComponents?.length > 0
    ? storeComponents
    : (sa_output?.components || sa_output?.data?.components || []);

  if (components.length === 0) return <div className="h-full flex items-center justify-center text-slate-500">컴포넌트 데이터 없음</div>;

  const frontend = components.filter(c => c.domain === "Frontend" || c.domain === "F" || c.domain?.toLowerCase().includes("front"));
  const backend = components.filter(c => c.domain === "Backend" || c.domain === "B" || c.domain?.toLowerCase().includes("back"));

  return (
    <ReportLayout
      icon={Layers}
      title="System Components"
      subtitle="아키텍처를 구성하는 각 서비스 유닛과 UI 모듈의 역할 및 의존성 설계입니다."
      badge={`${components.length} Units`}
      rightElement={<ShareArtifactButton kind="components" runId={resultData?.run_id || resultData?.metadata?.run_id} />}
    >
      {frontend.length > 0 && (
        <ReportSection title="Frontend Modules" icon={<Layout size={20} className="text-fuchsia-500" />} badge={frontend.length}>
          <div className="report-grid-3">
            {frontend.map((c, i) => <ComponentCard key={i} comp={c} isDarkMode={isDarkMode} />)}
          </div>
        </ReportSection>
      )}

      {backend.length > 0 && (
        <ReportSection title="Backend Services" icon={<Cpu size={20} className="text-cyan-500" />} badge={backend.length}>
          <div className="report-grid-3">
            {backend.map((c, i) => <ComponentCard key={i} comp={c} isDarkMode={isDarkMode} />)}
          </div>
        </ReportSection>
      )}
    </ReportLayout>
  );
}

const ComponentCard = React.memo(({ comp, isDarkMode }) => (
  <Card variant="glass" className="p-6 h-full flex flex-col hover:border-blue-500/50 transition-all hover:-translate-y-1">
    <div className="flex items-center gap-2 mb-4">
      <Box className="text-blue-500 shrink-0" size={18} />
      <h4 className={`font-bold tracking-tight truncate ${isDarkMode ? "text-white" : "text-slate-900"}`}>
        {comp.component_name}
      </h4>
    </div>
    <p className={`text-[14px] leading-relaxed mb-6 flex-1 ${isDarkMode ? "text-slate-400" : "text-slate-600"}`}>
      {comp.role}
    </p>
    {comp.dependencies?.length > 0 && (
      <div className="space-y-3 pt-4 border-t border-white/5">
        <div className="flex items-center gap-2">
          <GitBranch size={12} className="text-slate-500" />
          <span className="report-label-sm">Dependencies</span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {comp.dependencies.map((dep, i) => (
            <Badge key={i} variant="secondary" className="text-[10px] py-0 px-2 font-medium bg-blue-500/5 text-blue-400/80">
              {dep}
            </Badge>
          ))}
        </div>
      </div>
    )}
  </Card>
));

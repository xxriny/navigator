import React from "react";
import { useStore } from "../../store/useStore";
import { Globe, ArrowRight, Code, Terminal } from "lucide-react";
import Card from "../ui/Card";
import Badge from "../ui/Badge";
import ReportLayout from "./layout/ReportLayout";
import ShareArtifactButton from "./ShareArtifactButton";

export default function SAApiTab() {
  const { isDarkMode, sa_output, apis: storeApis, resultData } = useStore(['isDarkMode', 'sa_output', 'apis', 'resultData']);
  const apis = storeApis?.length > 0 ? storeApis : (sa_output?.apis || sa_output?.data?.apis || []);

  if (apis.length === 0) return <div className="h-full flex items-center justify-center text-slate-500">API 데이터 없음</div>;

  return (
    <ReportLayout
      icon={Globe}
      title="Interface Specifications"
      subtitle="서비스 간 통신을 위한 RESTful API 엔드포인트와 데이터 규격 설계입니다."
      badge={`${apis.length} Endpoints`}
      rightElement={<ShareArtifactButton kind="apis" runId={resultData?.run_id || resultData?.metadata?.run_id} />}
    >
      <div className="space-y-8">
        {apis.map((api, idx) => <ApiCard key={idx} api={api} isDarkMode={isDarkMode} />)}
      </div>
    </ReportLayout>
  );
}

const ApiCard = React.memo(({ api, isDarkMode }) => {
  const [method, path] = (api.endpoint || "GET /").split(" ");
  const methodVariant = method === "GET" ? "success" : method === "POST" ? "blue" : method === "DELETE" ? "error" : "warning";

  return (
    <Card variant="glass" className="p-8 space-y-8 hover:border-blue-500/30 transition-all">
      <div className="flex flex-col md:flex-row md:items-center gap-6 justify-between">
        <div className="flex items-center gap-4 overflow-hidden">
          <Badge variant={methodVariant} className="px-4 py-1.5 font-black shrink-0 text-sm">{method}</Badge>
          <code className={`text-xl font-bold font-mono truncate ${isDarkMode ? "text-blue-300" : "text-blue-700"}`}>
            {path}
          </code>
        </div>
        <p className={`text-[15px] italic font-medium ${isDarkMode ? "text-slate-500" : "text-slate-400"}`}>
          {api.description}
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr] gap-6 items-stretch">
        <SchemaViewer title="Request Schema" schema={api.request_schema} isDarkMode={isDarkMode} icon={<Terminal size={14}/>} />
        <div className="hidden lg:flex items-center justify-center opacity-20"><ArrowRight size={28} /></div>
        <SchemaViewer title="Response Schema" schema={api.response_schema} isDarkMode={isDarkMode} icon={<Code size={14}/>} />
      </div>
    </Card>
  );
});

const SchemaViewer = React.memo(({ title, schema, isDarkMode, icon }) => (
  <div className={`rounded-2xl border flex flex-col h-full overflow-hidden ${isDarkMode ? "bg-black/40 border-white/5" : "bg-slate-50 border-slate-200"}`}>
    <div className={`px-4 py-3 border-b flex items-center gap-2 ${isDarkMode ? "bg-white/5 border-white/5" : "bg-slate-100 border-slate-200"}`}>
      {icon}
      <span className="report-label-sm">{title}</span>
    </div>
    <div className="p-5 overflow-x-auto custom-scrollbar flex-1">
      <pre className={`font-mono text-[13px] leading-relaxed ${isDarkMode ? "text-blue-300/70" : "text-blue-600"}`}>
        {schema ? JSON.stringify(schema, null, 2) : "// No schema defined"}
      </pre>
    </div>
  </div>
));

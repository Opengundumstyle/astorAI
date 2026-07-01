import { DemoBanner } from "@/components/DemoBanner";
import { Sidebar } from "@/components/Sidebar";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex flex-1 flex-col">
        <DemoBanner />
        <main className="flex-1 overflow-auto p-8">{children}</main>
      </div>
    </div>
  );
}

import { IngestForm } from "@/components/IngestForm";
import { ProductsTable } from "@/components/ProductsTable";

export default function IngestPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-bold">Ingest &amp; Browse</h1>
      <IngestForm />
      <ProductsTable />
    </div>
  );
}

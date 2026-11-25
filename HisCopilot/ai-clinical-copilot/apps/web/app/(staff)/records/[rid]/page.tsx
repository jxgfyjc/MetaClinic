"use client";
import { use, useEffect } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { ErrorBox, Spinner } from "@/components/ui";

// A record always lives inside its encounter workspace: one place to read, edit, finalise and print it.
export default function RecordRedirect({ params }: { params: Promise<{ rid: string }> }) {
  const { rid } = use(params); const router = useRouter();
  const { data, error, mutate } = useSWR(`/records/${rid}`);
  useEffect(() => { if (data) router.replace(`/encounters/${data.record.encounter_id}?step=record`); }, [data, router]);
  if (error) return <ErrorBox error={error} title="无法打开病历" onRetry={() => mutate()} />;
  return <div className="grid h-64 place-items-center text-ink-300"><Spinner size={22} /></div>;
}

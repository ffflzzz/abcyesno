import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { App, Button } from "antd";
import { Download, FileUp, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { readZip } from "@/lib/zip";
import { setMediaBlob } from "@/services/file-storage";
import { setImageBlob } from "@/services/image-storage";
import { CanvasDeleteProjectsDialog } from "@/components/canvas/canvas-delete-projects-dialog";
import { CanvasProjectCard } from "@/components/canvas/canvas-project-card";
import type { CanvasExportFile } from "@/types/canvas-export";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useCanvasUiStore } from "@/stores/canvas/use-canvas-ui-store";
import { exportCanvasProjects } from "@/lib/canvas/canvas-export";

export default function CanvasPage() {
    const { message } = App.useApp();
    const { t } = useTranslation();
    const navigate = useNavigate();
    const inputRef = useRef<HTMLInputElement>(null);
    const hydrated = useCanvasStore((state) => state.hydrated);
    const projects = useCanvasStore((state) => state.projects);
    const createProject = useCanvasStore((state) => state.createProject);
    const importProject = useCanvasStore((state) => state.importProject);
    const selectedIds = useCanvasUiStore((state) => state.selectedProjectIds);
    const setDeleteIds = useCanvasUiStore((state) => state.setDeleteProjectIds);

    const enterProject = (id: string) => {
        navigate(`/canvas/${id}`);
    };
    const createAndEnter = () => enterProject(createProject(t("canvas.defaultTitle", { count: projects.length + 1 })));

    /**
     * 从网址直接打开：`/canvas?from=<接口地址>` ⇒ 拉取、导入、立刻进入那张画布。
     *
     * 给外部系统用（shortdrama 后端按"已跑完的集"生成的画布 JSON），
     * 省掉"导出文件 → 手动导入"这一步。失败要响亮报出来，不能停在空列表上让人猜。
     */
    const autoImported = useRef(false);
    useEffect(() => {
        const src = new URLSearchParams(window.location.search).get("from");
        if (!src || !hydrated || autoImported.current) return;
        autoImported.current = true;
        void fetch(src)
            .then((res) => (res.ok ? res.json() : Promise.reject(new Error("HTTP " + res.status))))
            .then((body) => {
                const data = (body && body.data) || body;
                if (!data || !Array.isArray(data.nodes) || !data.nodes.length) throw new Error("接口返回里没有节点");
                const id = importProject(data);
                message.success(`已导入「${data.title}」：${data.nodes.length} 个节点`);
                navigate(`/canvas/${id}`, { replace: true });
            })
            .catch((err: unknown) => message.error(`画布导入失败：${err instanceof Error ? err.message : String(err)}`));
    }, [hydrated, importProject, message, navigate]);

    /**
     * 直接进画布：`?go=last` ⇒ **跳过这个库页**，开最近一张画布；一张都没有就新建一张。
     *
     * 为什么需要（2026-10-08 用户原话：「不要进第一个截图这个页面，而是直接进第二个
     * 截图的这个页面」）：shortdrama 的宿主那栏要的是一张**能直接画**的纸，
     * 而 `/canvas` 是**库**页、`/canvas/<id>` 才是画布 —— 把人先丢到库页、
     * 再让他点「新建画布」是多余的一步。
     *
     * 为什么是"最近一张"而不是"每次都新建"：宿主每切一次项目就会重挂 iframe，
     * 每次新建会攒出一堆空画布（实测已经有"创作画布 1／2"）。要新的一张，
     * 库页上那个「新建画布」按钮一直都在。
     * 不带这个参数时行为与改造前**一字不变**。
     */
    const autoOpened = useRef(false);
    useEffect(() => {
        const go = new URLSearchParams(window.location.search).get("go");
        if (go !== "last" || !hydrated || autoOpened.current) return;
        autoOpened.current = true;
        const latest = [...projects].sort((a, b) =>
            String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")))[0];
        const id = latest ? latest.id : createProject(t("canvas.defaultTitle", { count: projects.length + 1 }));
        navigate(`/canvas/${id}`, { replace: true });
    }, [hydrated, projects, createProject, navigate, t]);

    const importCanvas = async (file?: File) => {
        if (!file) return;
        try {
            const zip = await readZip(file);
            const projectFile = zip.get("projects.json");
            if (!projectFile) throw new Error("missing projects.json");
            const data = JSON.parse(await projectFile.text()) as CanvasExportFile;
            await Promise.all(
                data.projects.flatMap((project) =>
                    project.files.map(async (item) => {
                        const blob = zip.get(item.path);
                        if (!blob) return;
                        const typedBlob = blob.type ? blob : blob.slice(0, blob.size, item.mimeType);
                        await (item.storageKey.startsWith("image:") ? setImageBlob(item.storageKey, typedBlob) : setMediaBlob(item.storageKey, typedBlob));
                    }),
                ),
            );
            data.projects.forEach((item) => importProject(item.project));
            message.success(t("canvas.imported", { count: data.projects.length }));
        } catch {
            message.error(t("canvas.importFailed"));
        } finally {
            if (inputRef.current) inputRef.current.value = "";
        }
    };

    return (
        <main className="h-full overflow-auto bg-background text-stone-950 dark:text-stone-100">
            <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-6 py-10">
                <header className="flex flex-wrap items-end justify-between gap-4 border-b border-stone-200 pb-6 dark:border-stone-800">
                    <div>
                        <p className="text-xs text-stone-500">{t("canvas.library")}</p>
                        <h1 className="mt-3 text-3xl font-semibold">{t("canvas.title")}</h1>
                    </div>
                    <div className="flex items-center gap-2">
                        {selectedIds.length ? (
                            <>
                                <Button disabled={!hydrated} icon={<Download className="size-4" />} onClick={() => void exportCanvasProjects(projects.filter((project) => selectedIds.includes(project.id)), `${t("canvas.title")}-${selectedIds.length}`)}>
                                    {t("canvas.exportSelected")}
                                </Button>
                                <Button disabled={!hydrated} onClick={() => setDeleteIds(selectedIds)}>
                                    {t("canvas.deleteSelected")}
                                </Button>
                            </>
                        ) : null}
                        {projects.length ? (
                            <Button disabled={!hydrated} onClick={() => setDeleteIds(projects.map((project) => project.id))}>
                                {t("canvas.deleteAll")}
                            </Button>
                        ) : null}
                        <Button disabled={!hydrated} icon={<FileUp className="size-4" />} onClick={() => inputRef.current?.click()}>
                            {t("canvas.import")}
                        </Button>
                        <Button disabled={!hydrated} type="primary" icon={<Plus className="size-4" />} onClick={createAndEnter}>
                            {t("canvas.create")}
                        </Button>
                    </div>
                </header>

                {!hydrated ? (
                    <section className="flex min-h-[360px] items-center justify-center border-y border-stone-200 text-sm text-stone-500 dark:border-stone-800">{t("canvas.loading")}</section>
                ) : projects.length ? (
                    <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
                        {projects.map((project) => (
                            <CanvasProjectCard key={project.id} project={project} />
                        ))}
                    </div>
                ) : (
                    <section className="flex min-h-[360px] flex-col items-center justify-center border-y border-stone-200 text-center dark:border-stone-800">
                        <h2 className="text-xl font-medium">{t("canvas.empty")}</h2>
                        <p className="mt-3 text-sm text-stone-500">{t("canvas.emptyDescription")}</p>
                        <Button type="primary" className="mt-6" icon={<Plus className="size-4" />} onClick={createAndEnter}>
                            {t("canvas.create")}
                        </Button>
                    </section>
                )}
            </div>

            <input ref={inputRef} type="file" accept="application/zip,.zip" className="hidden" onChange={(event) => void importCanvas(event.target.files?.[0])} />
            <CanvasDeleteProjectsDialog />
        </main>
    );
}

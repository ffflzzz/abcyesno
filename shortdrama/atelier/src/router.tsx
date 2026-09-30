import { createBrowserRouter, Outlet } from "react-router-dom";

import UserLayout from "@/layouts/user-layout";
import AssetsPage from "@/pages/assets";
import CanvasPage from "@/pages/canvas";
import CanvasProjectPage from "@/pages/canvas/project";
import ConfigPage from "@/pages/config";
import DirectorPage from "@/pages/director";
import HomePage from "@/pages/home";
import NotFound from "@/pages/not-found";

export const router = createBrowserRouter([
    {
        element: (
            <UserLayout>
                <Outlet />
            </UserLayout>
        ),
        children: [
            { path: "/", element: <HomePage /> },
            { path: "/assets", element: <AssetsPage /> },
            { path: "/canvas", element: <CanvasPage /> },
            { path: "/canvas/:id", element: <CanvasProjectPage /> },
            { path: "/director", element: <DirectorPage /> },
            { path: "/config", element: <ConfigPage /> },
        ],
    },
    { path: "*", element: <NotFound /> },
], {
    /**
     * ★ 挂到宿主站点的**子路径**下时必须给 basename。
     *
     * 本仓库把它同源挂在 `/atelier/`（`v5/server.py`），构建期 `VITE_BASE=/atelier/`；
     * 不给 basename 的话应用内部跳转（`/canvas`、`/config`…）会打到宿主站的根 ⇒ 404。
     * 直接读 vite 注入的 `BASE_URL`，让"构建前缀"和"路由前缀"只有**一个来源**。
     */
    basename: import.meta.env.BASE_URL.replace(/\/+$/, "") || "/",
});

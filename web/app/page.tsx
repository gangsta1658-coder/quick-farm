"use client";

import { useEffect, useState } from "react";
import { api, clearConn, loadConn, saveConn, type Conn } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  OverviewPanel,
  RegisterPanel,
  SettingsPanel,
  TasksPanel,
} from "@/components/panels";
import { LogOut, Sprout } from "lucide-react";

export default function Home() {
  const [conn, setConn] = useState<Conn | null>(null);
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState("overview");

  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const [connecting, setConnecting] = useState(false);

  useEffect(() => {
    setConn(loadConn());
    setReady(true);
  }, []);

  const connect = async () => {
    setConnecting(true);
    setErr("");
    const candidate = { base: "/backend", token: token.trim() };
    try {
      await api.health(candidate.base);
      await api.getConfig(candidate); // validates the token
      saveConn(candidate);
      setConn(candidate);
    } catch (e) {
      setErr(
        "连不上或令牌不对：" + (e instanceof Error ? e.message : String(e)),
      );
    } finally {
      setConnecting(false);
    }
  };

  const disconnect = () => {
    clearConn();
    setConn(null);
    setToken("");
  };

  if (!ready) return null;

  if (!conn) {
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-4">
        <Card className="rounded-[2rem] shadow-md">
          <CardHeader>
            <div className="mb-2 flex items-center gap-2 text-muted-foreground">
              <Sprout className="h-5 w-5" />
              <span className="text-xs font-medium tracking-widest uppercase">
                Quick Farm
              </span>
            </div>
            <CardTitle className="text-2xl tracking-tight">
              连接后端
            </CardTitle>
            <CardDescription>
              前后端同域部署，只填管理员令牌就能进。令牌在后端
              server/config.json 里的 admin_token。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>管理员令牌</Label>
              <Input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="admin_token"
              />
            </div>
            {err && <p className="text-sm text-destructive">{err}</p>}
            <Button
              className="w-full rounded-full"
              onClick={connect}
              disabled={connecting || !token.trim()}
            >
              {connecting ? "连接中" : "连接"}
            </Button>
          </CardContent>
        </Card>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-muted-foreground">
            <Sprout className="h-4 w-4" />
            <span className="text-xs font-medium tracking-widest uppercase">
              Quick Farm
            </span>
          </div>
          <h1 className="mt-1 text-3xl font-medium tracking-tight">
            注册机控制台
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">已连接</p>
        </div>
        <Button variant="outline" size="sm" onClick={disconnect}>
          <LogOut className="mr-1 h-4 w-4" /> 断开
        </Button>
      </header>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-5">
          <TabsTrigger value="overview">总览</TabsTrigger>
          <TabsTrigger value="register">注册</TabsTrigger>
          <TabsTrigger value="tasks">任务</TabsTrigger>
          <TabsTrigger value="settings">设置</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <OverviewPanel conn={conn} goTasks={() => setTab("tasks")} />
        </TabsContent>
        <TabsContent value="register">
          <RegisterPanel conn={conn} goTasks={() => setTab("tasks")} />
        </TabsContent>
        <TabsContent value="tasks">
          <TasksPanel conn={conn} />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsPanel conn={conn} />
        </TabsContent>
      </Tabs>
    </main>
  );
}

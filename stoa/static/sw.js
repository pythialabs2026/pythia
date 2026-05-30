// Stoa SW v3 — Web Push 전용 (네비게이션/요청 가로채기 없음).
// 과거 fetch 핸들러가 캐시를 채우지도 않으면서 네비게이션을 가로채 로그인 후
// 이동(GET /)이 막히는 버그 → fetch 리스너 제거. 모든 요청은 브라우저가 직접 네트워크로.
self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil((async () => {
  // 옛 SW가 캐시해둔 응답 전부 폐기 (로그인 페이지가 캐시에 박혀있을 수 있음)
  for (const k of await caches.keys()) await caches.delete(k);
  await self.clients.claim();
})()));

// 서버 푸시 수신 → 알림 표시 (앱이 꺼져 있어도 동작)
self.addEventListener("push", e => {
  let data = { title: "Stoa", body: "새 신호", tag: "stoa" };
  try { if (e.data) data = Object.assign(data, e.data.json()); } catch (_) {}
  e.waitUntil(self.registration.showNotification(data.title, {
    body: data.body,
    tag: data.tag,
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    renotify: true,
    data: { url: data.url || "/" },
  }));
});

// 알림 클릭 → 앱 포커스/열기
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
    for (const c of list) { if ("focus" in c) return c.focus(); }
    if (clients.openWindow) return clients.openWindow(url);
  }));
});

/**
 * @file 用户主页
 */
(() => {
  insert_channels_style();
  
  // 解决主页加载时 my_username 初始化的竞态条件
  var my_username = typeof __wx_username !== "undefined" ? __wx_username : "";

  // 缓存当前页面已加载 of feeds 和原始 objects 数据，及最后的 lastBuffer 游标
  let cachedFeeds = [];
  let cachedRawObjects = [];
  let pageLastBuffer = "";

  if (typeof WXU !== "undefined" && WXU.onUserFeedsLoaded) {
    WXU.onUserFeedsLoaded((data) => {
      let rawList = [];
      let buf = "";
      if (Array.isArray(data)) {
        rawList = data;
      } else if (data && Array.isArray(data.feeds)) {
        rawList = data.feeds;
        buf = data.lastBuffer || "";
      }
      
      if (buf) {
        pageLastBuffer = buf;
      }
      
      rawList.forEach((obj) => {
        const feed = WXU.format_feed(obj);
        if (feed && feed.type !== "live") {
          if (!cachedFeeds.some((f) => f.id === feed.id)) {
            cachedFeeds.push(feed);
            cachedRawObjects.push(obj);
          }
        }
      });
    });
  }

  /**
   * --- 悬浮进度提示卡 (ProgressCard) ---
   */
  let $progressCard = null;
  function showProgressCard(title, text) {
    if (!$progressCard) {
      $progressCard = document.createElement("div");
      $progressCard.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 100000;
        background: rgba(30, 30, 35, 0.95);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        color: #fff;
        padding: 14px 20px;
        border-radius: 12px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.35);
        border: 1px solid rgba(255,255,255,0.15);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        min-width: 280px;
        max-width: 340px;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
        transform: translateY(-20px);
        opacity: 0;
      `;
      document.body.appendChild($progressCard);
      // 触发动画
      setTimeout(() => {
        if ($progressCard) {
          $progressCard.style.transform = "translateY(0)";
          $progressCard.style.opacity = "1";
        }
      }, 50);
    }
    
    $progressCard.innerHTML = `
      <div style="font-weight: 600; font-size: 14px; margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
        <span class="wx-spin-loader" style="display: inline-block; width: 14px; height: 14px; border: 2px solid #07C160; border-top-color: transparent; border-radius: 50%; animation: wx-spin 1s linear infinite; flex-shrink: 0;"></span>
        <span>${title}</span>
      </div>
      <div style="font-size: 12px; color: #ccc; line-height: 1.4; word-break: break-all;">${text}</div>
    `;
    
    // 添加 CSS 旋转动画
    if (!document.getElementById("wx-progress-style")) {
      const style = document.createElement("style");
      style.id = "wx-progress-style";
      style.textContent = `
        @keyframes wx-spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      `;
      document.head.appendChild(style);
    }
  }

  function hideProgressCard(finalTitle, finalText, duration = 3000) {
    if (!$progressCard) return;
    
    const isErrorOrCancel = finalTitle.includes("取消") || finalTitle.includes("失败");
    const iconColor = isErrorOrCancel ? "#ff4d4f" : "#07C160";
    const icon = isErrorOrCancel ? "✗" : "✓";
    
    $progressCard.innerHTML = `
      <div style="font-weight: 600; font-size: 14px; margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
        <span style="color: ${iconColor}; font-weight: bold; flex-shrink: 0;">${icon}</span>
        <span>${finalTitle}</span>
      </div>
      <div style="font-size: 12px; color: #ccc; word-break: break-all;">${finalText}</div>
    `;
    
    setTimeout(() => {
      if ($progressCard) {
        $progressCard.style.transform = "translateY(-20px)";
        $progressCard.style.opacity = "0";
        setTimeout(() => {
          if ($progressCard) {
            $progressCard.remove();
            $progressCard = null;
          }
        }, 300);
      }
    }, duration);
  }

  /**
   * 获取当前页 username
   */
  function __wx_get_username() {
    var { href } = window.location;
    if (!href) return null;
    const queries = WXU.get_queries(href);
    return queries.username || null;
  }

  /**
   * 加载一页 feeds（返回格式化后的 feeds + 原始 objects 用于同步）
   * @param {string} username
   * @param {string} lastBuffer
   * @returns {Promise<{feeds: Array, rawObjects: Array, lastBuffer: string, hasMore: boolean} | null>}
   */
  async function __wx_load_one_page(username, lastBuffer) {
    if (!my_username && typeof __wx_username !== "undefined" && __wx_username) {
      my_username = __wx_username;
    }
    var payload = {
      username: username,
      finderUsername: my_username || username,
      lastBuffer: lastBuffer,
      needFansCount: 0,
      objectId: "0",
    };
    var r = await WXU.API.finderUserPage(payload);
    if (r.errCode !== 0) {
      WXU.error({ msg: r.errMsg, alert: 0 });
      return null;
    }
    const rawObjects = r.data.object || [];
    const feeds = rawObjects
      .map((obj) => WXU.format_feed(obj))
      .filter((feed) => feed && feed.type !== "live");
    const hasMore = !!(r.data.lastBuffer && rawObjects.length >= 15);
    return { feeds, rawObjects, lastBuffer: r.data.lastBuffer || "", hasMore };
  }

  /**
   * 打开批量下载弹窗（立即弹出，默认从页面缓存加载，支持下拉加载更多）
   */
  function __wx_open_batch_panel() {
    if (!WXU.API.finderUserPage) {
      WXU.error({ msg: "API 未完成初始化" });
      return;
    }
    const username = __wx_get_username();
    if (!username) {
      WXU.error({ msg: "username 不能为空" });
      return;
    }

    let allFeeds = [];
    let selectedSet = new Set();
    let nextMarker = pageLastBuffer;
    let hasMore = true;
    let isLoadingMore = false;
    let isCancelled = false;
    let isDownloading = false;

    // 立即创建弹窗
    const $overlay = document.createElement("div");
    $overlay.style.cssText =
      "position:fixed;top:0;left:0;right:0;bottom:0;z-index:99999;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;";

    $overlay.innerHTML = `
      <div style="background:var(--popup-bg-color, #fff);border-radius:12px;width:440px;max-width:90vw;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.3);overflow:hidden;">
        <div style="padding:14px 20px;border-bottom:1px solid rgba(0,0,0,0.1);display:flex;justify-content:space-between;align-items:center;">
          <div style="font-size:15px;font-weight:600;">批量下载</div>
          <div style="font-size:13px;color:#888;" id="wx-dl-count">加载中...</div>
        </div>
        <div style="padding:6px 16px;display:flex;gap:6px;border-bottom:1px solid rgba(0,0,0,0.06);flex-wrap:wrap;">
          <button id="wx-dl-selall" class="button weui-btn weui-btn_default weui-btn_mini" style="font-size:12px;">全选</button>
          <button id="wx-dl-selnone" class="button weui-btn weui-btn_default weui-btn_mini" style="font-size:12px;">全不选</button>
          <button id="wx-dl-selinv" class="button weui-btn weui-btn_default weui-btn_mini" style="font-size:12px;">反选</button>
        </div>
        <div id="wx-dl-list" style="flex:1;overflow-y:auto;min-height:180px;max-height:50vh;">
          <div style="text-align:center;padding:30px;color:#888;font-size:13px;">正在加载作品列表...</div>
        </div>
        <div style="padding:10px 16px;border-top:1px solid rgba(0,0,0,0.1);display:flex;justify-content:space-between;gap:6px;flex-wrap:wrap;">
          <button id="wx-dl-all" class="button weui-btn weui-btn_primary weui-btn_mini" style="font-size:12px;">下载作者所有作品</button>
          <div style="display:flex;gap:6px;">
            <button id="wx-dl-cancel" class="button weui-btn weui-btn_default weui-btn_mini" style="font-size:13px;">取消</button>
            <button id="wx-dl-confirm" class="button weui-btn weui-btn_primary weui-btn_mini" style="font-size:13px;" disabled>下载已选</button>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild($overlay);

    const $list = $overlay.querySelector("#wx-dl-list");
    const $count = $overlay.querySelector("#wx-dl-count");
    const $confirmBtn = $overlay.querySelector("#wx-dl-confirm");
    const $loadMoreBtn = document.createElement("div");

    function updateCount() {
      $count.textContent = `已选 ${selectedSet.size}/${allFeeds.length}`;
      $confirmBtn.textContent = `下载已选 (${selectedSet.size})`;
      $confirmBtn.disabled = selectedSet.size === 0;
    }

    function renderItem(feed, idx) {
      const title = feed.title || feed.description || "无标题";
      const safeTitle = title.length > 50 ? title.slice(0, 50) + "…" : title;
      const checked = selectedSet.has(idx) ? "checked" : "";
      return `<div style="display:flex;align-items:center;gap:8px;padding:7px 12px;border-bottom:1px solid rgba(0,0,0,0.04);cursor:pointer;" data-idx="${idx}">
        <input type="checkbox" ${checked} style="width:17px;height:17px;cursor:pointer;flex-shrink:0;">
        <span style="flex:1;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${safeTitle}">${safeTitle}</span>
      </div>`;
    }

    function appendFeeds(feeds, startIdx) {
      if ($loadMoreBtn.parentNode) $loadMoreBtn.remove();
      feeds.forEach((feed, i) => {
        const idx = startIdx + i;
        const div = document.createElement("div");
        div.innerHTML = renderItem(feed, idx);
        $list.appendChild(div.firstChild);
      });
      if (hasMore) {
        $loadMoreBtn.innerHTML = `<div style="text-align:center;padding:12px;cursor:pointer;color:var(--weui-LINK,#576b95);font-size:13px;font-weight:500;">⬇ 加载更多作品...</div>`;
        $list.appendChild($loadMoreBtn);
      } else {
        $loadMoreBtn.innerHTML = `<div style="text-align:center;padding:12px;color:#888;font-size:13px;">没有更多了</div>`;
        $list.appendChild($loadMoreBtn);
      }
    }

    // 加载更多
    async function loadMore() {
      if (isLoadingMore || !hasMore || isCancelled) return;
      isLoadingMore = true;
      $loadMoreBtn.innerHTML = `<div style="text-align:center;padding:12px;color:#888;font-size:13px;">加载中...</div>`;
      try {
        const page = await __wx_load_one_page(username, nextMarker);
        if (!page || isCancelled) return;
        const startIdx = allFeeds.length;
        allFeeds.push(...page.feeds);
        page.feeds.forEach((_, i) => selectedSet.add(startIdx + i));
        nextMarker = page.lastBuffer;
        hasMore = page.hasMore;
        appendFeeds(page.feeds, startIdx);
        updateCount();
      } catch (ex) {
        $loadMoreBtn.innerHTML = `<div style="text-align:center;padding:12px;color:#f00;font-size:13px;">加载失败，点击重试</div>`;
      } finally {
        isLoadingMore = false;
      }
    }

    $loadMoreBtn.addEventListener("click", loadMore);
    
    // 监听列表滚动实现下拉加载更多
    $list.addEventListener("scroll", () => {
      if ($list.scrollHeight - $list.scrollTop - $list.clientHeight < 40) {
        loadMore();
      }
    });

    function close() {
      isCancelled = true;
      $overlay.remove();
    }

    // 事件代理：点击行 toggle 选中
    $list.addEventListener("click", (e) => {
      if ($loadMoreBtn.contains(e.target)) return;
      const row = e.target.closest("[data-idx]");
      if (!row) return;
      const idx = parseInt(row.dataset.idx, 10);
      const cb = row.querySelector("input[type=checkbox]");
      if (e.target === cb) {
        if (cb.checked) selectedSet.add(idx);
        else selectedSet.delete(idx);
      } else {
        if (selectedSet.has(idx)) {
          selectedSet.delete(idx);
          cb.checked = false;
        } else {
          selectedSet.add(idx);
          cb.checked = true;
        }
      }
      updateCount();
    });

    // 全选/全不选/反选
    $overlay.querySelector("#wx-dl-selall").onclick = () => {
      allFeeds.forEach((_, i) => selectedSet.add(i));
      $list.querySelectorAll("[data-idx] input[type=checkbox]").forEach((cb) => (cb.checked = true));
      updateCount();
    };
    $overlay.querySelector("#wx-dl-selnone").onclick = () => {
      selectedSet.clear();
      $list.querySelectorAll("[data-idx] input[type=checkbox]").forEach((cb) => (cb.checked = false));
      updateCount();
    };
    $overlay.querySelector("#wx-dl-selinv").onclick = () => {
      allFeeds.forEach((_, i) => {
        if (selectedSet.has(i)) selectedSet.delete(i);
        else selectedSet.add(i);
      });
      $list.querySelectorAll("[data-idx] input[type=checkbox]").forEach((cb, i) => {
        cb.checked = selectedSet.has(i);
      });
      updateCount();
    };

    // 取消
    $overlay.querySelector("#wx-dl-cancel").onclick = close;

    // 带悬浮进度卡提示的批量下载
    async function doBatchDownload(feeds) {
      isDownloading = true;
      close();
      const total = feeds.length;
      showProgressCard("批量下载中", `准备下载，共 ${total} 个视频...`);
      let completed = 0;
      let failed = 0;
      for (let i = 0; i < feeds.length; i++) {
        const feed = feeds[i];
        const rawTitle = feed.title || feed.description || "视频";
        const displayTitle = rawTitle.length > 15 ? rawTitle.slice(0, 15) + "…" : rawTitle;
        
        showProgressCard(
          "批量下载中", 
          `[${i + 1}/${total}] 正在下载: ${displayTitle}<br>已完成 ${completed} (失败 ${failed})`
        );
        
        try {
          var [err, res] = await WXU.request({
            method: "POST",
            url: "/__wx_channels_api/download",
            body: {
              url: feed.url,
              description: feed.title || "",
              createtime: String(feed.createtime || 0),
              key: String(feed.key || ""),
            },
          });
          if (err) {
            failed++;
          } else {
            completed++;
          }
        } catch (e) {
          failed++;
        }
      }
      hideProgressCard("批量下载完成", `成功 ${completed}，失败 ${failed}`);
      isDownloading = false;
    }

    // 下载已选
    $overlay.querySelector("#wx-dl-confirm").onclick = async () => {
      const selected = allFeeds.filter((_, i) => selectedSet.has(i));
      if (selected.length === 0) {
        WXU.toast("未选择任何视频");
        return;
      }
      await doBatchDownload(selected);
    };

    // 下载作者所有作品
    $overlay.querySelector("#wx-dl-all").onclick = async () => {
      const $allBtn = $overlay.querySelector("#wx-dl-all");
      $allBtn.disabled = true;
      $allBtn.textContent = "正在加载所有作品...";

      // 先加载剩余所有页
      while (hasMore && !isCancelled) {
        const page = await __wx_load_one_page(username, nextMarker);
        if (!page || isCancelled) break;
        const startIdx = allFeeds.length;
        allFeeds.push(...page.feeds);
        page.feeds.forEach((_, i) => selectedSet.add(startIdx + i));
        appendFeeds(page.feeds, startIdx);
        nextMarker = page.lastBuffer;
        hasMore = page.hasMore;
        updateCount();
      }

      if (isCancelled) return;
      if (allFeeds.length === 0) {
        WXU.toast("没有找到可下载的视频");
        return;
      }
      await doBatchDownload(allFeeds);
    };

    // 初始化加载第一页，优先从缓存获取
    (() => {
      if (cachedFeeds.length > 0) {
        $list.innerHTML = "";
        allFeeds.push(...cachedFeeds);
        cachedFeeds.forEach((_, i) => selectedSet.add(i)); // 默认全选
        appendFeeds(cachedFeeds, 0);
        updateCount();
      } else {
        (async () => {
          try {
            const page = await __wx_load_one_page(username, "");
            if (!page || isCancelled) {
              if (!isCancelled) {
                $list.innerHTML = '<div style="text-align:center;padding:30px;color:#f00;font-size:13px;">加载失败</div>';
              }
              return;
            }
            $list.innerHTML = "";
            allFeeds.push(...page.feeds);
            page.feeds.forEach((_, i) => selectedSet.add(i));
            nextMarker = page.lastBuffer;
            hasMore = page.hasMore;
            appendFeeds(page.feeds, 0);
            updateCount();

            if (allFeeds.length === 0) {
              $list.innerHTML = '<div style="text-align:center;padding:30px;color:#888;font-size:13px;">没有找到视频作品</div>';
            }
          } catch (ex) {
            if (!isCancelled) {
              $list.innerHTML = `<div style="text-align:center;padding:30px;color:#f00;font-size:13px;">加载失败: ${ex.message || ex}</div>`;
            }
          }
        })();
      }
    })();
  }

  /**
   * 同步作者作品到工具后端（使用 ProgressCard 进度，可取消）
   */
  async function __wx_sync_author($syncBtn) {
    if (!WXU.API.finderUserPage) {
      WXU.error({ msg: "API 未完成初始化" });
      return;
    }
    const username = __wx_get_username();
    if (!username) {
      WXU.error({ msg: "username 不能为空" });
      return;
    }

    let cancelled = false;
    let totalSynced = 0;
    let pageNum = 0;

    // 按钮变为取消态
    const origText = $syncBtn.innerText;
    const origOnclick = $syncBtn.onclick;
    $syncBtn.innerText = "取消同步";
    $syncBtn.onclick = () => {
      cancelled = true;
      $syncBtn.innerText = "取消中...";
      $syncBtn.disabled = true;
    };

    showProgressCard("同步作者作品中", "正在开始同步...");

    const restore = () => {
      $syncBtn.innerText = origText;
      $syncBtn.onclick = origOnclick;
      $syncBtn.disabled = false;
    };

    try {
      // 1. 同步已经在页面上缓存的原始 rawObjects
      if (cachedRawObjects.length > 0) {
        var rawVideoObjects = cachedRawObjects.filter((obj) => {
          if (!obj.objectDesc) return false;
          return obj.objectDesc.mediaType === 4;
        });
        if (rawVideoObjects.length > 0) {
          await WXU.request({
            method: "POST",
            url: "/__wx_channels_api/sync-feed",
            body: { username, feeds: rawVideoObjects },
          });
        }
        totalSynced += rawVideoObjects.length;
        showProgressCard("同步作者作品中", `已同步页面已加载的 ${totalSynced} 个视频作品...`);
      }

      // 2. 然后继续加载剩余页
      let nextMarker = pageLastBuffer;
      let hasMore = true;
      
      // 如果没有已缓存的数据，说明还没加载，从头开始
      if (cachedFeeds.length === 0) {
        nextMarker = "";
      }

      while (hasMore && !cancelled) {
        pageNum++;
        showProgressCard(
          "同步作者作品中", 
          `正在加载第 ${pageNum} 页数据...<br>已同步 ${totalSynced} 个作品`
        );
        
        const page = await __wx_load_one_page(username, nextMarker);
        if (!page || cancelled) break;

        if (page.feeds.length > 0) {
          var rawVideoObjects = page.rawObjects.filter((obj) => {
            if (!obj.objectDesc) return false;
            return obj.objectDesc.mediaType === 4;
          });
          if (rawVideoObjects.length > 0) {
            await WXU.request({
              method: "POST",
              url: "/__wx_channels_api/sync-feed",
              body: { username, feeds: rawVideoObjects },
            });
          }
          totalSynced += rawVideoObjects.length;
        }

        nextMarker = page.lastBuffer;
        hasMore = page.hasMore;
      }

      if (cancelled) {
        hideProgressCard("同步已取消", `已同步 ${totalSynced} 个作品`);
      } else {
        hideProgressCard("同步完成", `共同步 ${totalSynced} 个作品`);
      }
    } catch (ex) {
      WXU.error({ msg: "同步失败: " + (ex.message || ex) });
      hideProgressCard("同步失败", ex.message || ex);
    } finally {
      restore();
    }
  }

  /**
   * 插入操作按钮
   */
  function __wx_insert_buttons() {
    const $operation = document.querySelector(".opr-area");
    if (!$operation) return false;
    if (document.getElementById("wx-batch-download-btn")) return true;

    // 创建按钮容器，保证两个按钮紧挨着
    const $wrap = document.createElement("span");
    $wrap.style.cssText = "display:inline-flex;gap:4px;margin-left:8px;";

    // --- 批量下载按钮 ---
    const $btn = document.createElement("button");
    $btn.id = "wx-batch-download-btn";
    $btn.className = "button h-7 weui-btn weui-btn_default weui-btn_mini";
    $btn.innerText = "批量下载";
    $btn.onclick = () => __wx_open_batch_panel();

    // --- 同步作者作品按钮 ---
    const $syncBtn = document.createElement("button");
    $syncBtn.id = "wx-sync-author-btn";
    $syncBtn.className = "button h-7 weui-btn weui-btn_default weui-btn_mini";
    $syncBtn.innerText = "同步作品";
    $syncBtn.onclick = () => __wx_sync_author($syncBtn);

    $wrap.appendChild($btn);
    $wrap.appendChild($syncBtn);
    $operation.appendChild($wrap);

    return true;
  }

  WXU.onInit((data) => {
    my_username = data.mainFinderUsername || my_username;
  });
  WXU.observe_node(".opr-area", () => {
    __wx_insert_buttons();
  });
})();

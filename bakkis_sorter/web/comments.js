/* BAKKIS MUSIC SORTER — In-app Feedback Comments & Ratings modal (PLN-07). */
"use strict";

import { api } from "./api.js";
import { toast } from "./toasts.js";

const $ = (id) => document.getElementById(id);

export const CommentsModal = {
  get modal() {
    return $("comments-modal");
  },
  get isOpen() {
    return this.modal && !this.modal.hidden;
  },

  async open() {
    if (!this.modal) return;
    this.modal.hidden = false;
    try {
      const data = await api("promo_comments_get");
      if (data) {
        if ($("select-rating-fav")) {
          $("select-rating-fav").value = String(data.promo_rating_favorite || 5);
        }
        if ($("select-rating-keep")) {
          $("select-rating-keep").value = String(data.promo_rating_keep || 4);
        }
        if ($("inp-promo-dl-dir")) {
          $("inp-promo-dl-dir").value = data.promo_download_dir || "~/Downloads";
        }
        if ($("txt-comments-fav")) {
          $("txt-comments-fav").value = (data.comments_5 || []).join("\n");
        }
        if ($("txt-comments-keep")) {
          $("txt-comments-keep").value = (data.comments_4 || []).join("\n");
        }
      }
    } catch (e) {
      toast("Failed to load promo comments: " + e.message, { level: "error" });
    }
  },

  close() {
    if (this.modal) this.modal.hidden = true;
  },

  async save() {
    const favRating = $("select-rating-fav") ? Number($("select-rating-fav").value) : 5;
    const keepRating = $("select-rating-keep") ? Number($("select-rating-keep").value) : 4;
    const promoDlDir = $("inp-promo-dl-dir") ? $("inp-promo-dl-dir").value.trim() : "~/Downloads";
    const favText = $("txt-comments-fav") ? $("txt-comments-fav").value : "";
    const keepText = $("txt-comments-keep") ? $("txt-comments-keep").value : "";

    const comments_5 = favText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    const comments_4 = keepText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);

    const saveBtn = $("btn-save-comments");
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";
    }

    try {
      await api("promo_comments_save", {
        comments_5,
        comments_4,
        promo_rating_favorite: favRating,
        promo_rating_keep: keepRating,
        promo_download_dir: promoDlDir,
      });
      if (window.refreshState) await window.refreshState();
      if (window.Drawers && window.Drawers.open === "settings" && window.SettingsDrawer) {
        window.SettingsDrawer.render();
      }
      toast("Comments & settings saved to database", { level: "info" });
      this.close();
    } catch (e) {
      toast("Failed to save comments: " + e.message, { level: "error" });
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = "Save Changes";
      }
    }
  },
};

export function initCommentsModal() {
  if ($("btn-edit-comments")) {
    $("btn-edit-comments").addEventListener("click", () => CommentsModal.open());
  }
  if ($("btn-empty-comments")) {
    $("btn-empty-comments").addEventListener("click", () => CommentsModal.open());
  }
  if ($("btn-choose-promo-dl-dir")) {
    $("btn-choose-promo-dl-dir").addEventListener("click", async () => {
      try {
        const folder = await api("choose_folder");
        if (folder && $("inp-promo-dl-dir")) {
          $("inp-promo-dl-dir").value = folder;
        }
      } catch (err) {
        toast("Folder chooser failed: " + err.message, { level: "error" });
      }
    });
  }
  if ($("btn-close-comments")) {
    $("btn-close-comments").addEventListener("click", () => CommentsModal.close());
  }
  if ($("btn-cancel-comments")) {
    $("btn-cancel-comments").addEventListener("click", () => CommentsModal.close());
  }
  if ($("btn-save-comments")) {
    $("btn-save-comments").addEventListener("click", () => CommentsModal.save());
  }
  if ($("comments-modal")) {
    $("comments-modal").addEventListener("click", (e) => {
      if (e.target === $("comments-modal")) CommentsModal.close();
    });
  }
}

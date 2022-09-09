const newProto = navigator.__proto__;
delete newProto.webdriver;
navigator.__proto__ = newProto;

(function () {
    let hCaptchaInstance;

    Object.defineProperty(window, "hcaptcha", {
        get: function () {
            return hCaptchaInstance;
        },
        set: function (e) {
            hCaptchaInstance = e;

            let originalRenderFunc = e.render;

            hCaptchaInstance.render = function (container, opts) {
                createHCaptchaWidget(container, opts);
                return originalRenderFunc(container, opts);
            };
        },
    });

    let createHCaptchaWidget = function (container, opts) {
        if (opts.callback !== undefined && typeof opts.callback === "function") {
            let key = "hcaptchaCallback" + Date.now();
            window[key] = opts.callback;
            opts.callback = key;
        }

        let info = {
            captchaType: "hcaptcha",
            widgetId: 0,
            containerId: container,
            sitekey: opts.sitekey,
            callback: opts.callback,
        };
        window["hcaptchaHandle"] = info;
    }
})();
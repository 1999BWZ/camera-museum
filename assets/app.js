// 首页类型筛选：点击类型 chip，过滤时间线卡片
document.addEventListener('DOMContentLoaded', function () {
  var chips = document.querySelectorAll('#type-filters .chip');
  if (!chips.length) return;
  var cards = document.querySelectorAll('#timeline .card');
  var decades = document.querySelectorAll('#timeline .decade');
  chips.forEach(function (ch) {
    ch.addEventListener('click', function () {
      chips.forEach(function (c) { c.classList.remove('active'); });
      ch.classList.add('active');
      var f = ch.getAttribute('data-filter');
      cards.forEach(function (card) {
        var show = !f || card.getAttribute('data-type') === f;
        card.style.display = show ? '' : 'none';
      });
      decades.forEach(function (d) {
        var visible = Array.prototype.filter.call(
          d.querySelectorAll('.card'),
          function (card) { return card.style.display !== 'none'; }
        ).length;
        d.style.display = visible ? '' : 'none';
      });
    });
  });
});

// Ручные истории Шанхая привязаны к идентификаторам мест из маршрута.
(() => {
  const stories = {
    'shanghai-demo:bund': {
      art:'viewpoints',
      history:'Вайтань — историческая набережная Шанхая вдоль реки Хуанпу. Этот район когда-то был финансовым центром города.',
      detail:'Пройдитесь вдоль воды и сравните две стороны реки. На Вайтане фасады исторических банков и офисов стоят рядом, поэтому набережную часто описывают как музей архитектуры под открытым небом.',
      source:'https://english.shanghai.gov.cn/en-HeritageZones/20231208/f2ac293f546a4d32aba936f2e733a47c.html',
    },
    'shanghai-demo:yu-garden': {
      art:'nature',
      history:'Юйюань — традиционный китайский сад в районе Хуанпу. Он находится рядом с храмом городского божества.',
      detail:'Выберите один небольшой участок сада и рассмотрите его с разных сторон. Обратите внимание на переходы между открытым пространством, павильонами и дорожками. Городской туристический портал выделяет Юйюань среди классических садов Шанхая.',
      source:'https://english.shanghai.gov.cn/en-Parks/20241118/3ab0a509201343a59102f9bc63a3aab4.html',
    },
    'shanghai-demo:shanghai-museum': {
      art:'museums',
      history:'Шанхайский музей на Народной площади хранит обширное собрание китайского искусства. Среди известных предметов коллекции — древний бронзовый треножник Да Кэ Дин.',
      detail:'Выберите в залах один предмет и начните с этикетки. Сопоставьте указанные там назначение и эпоху с тем, что видно без пояснений: формой, материалом и орнаментом.',
      source:'https://english.shanghai.gov.cn/en-MuseumsGalleries/20231218/01fdd3f62d0c4877bc289ec0b1de6afa.html',
    },
  };
  window.tripWiseGuideStory = (city, place) => {
    if (city !== 'shanghai' || !Object.hasOwn(stories, place.id)) return null;
    const story = stories[place.id];
    return {status:'available', language:'ru', art:story.art, short_text:story.history,
      full_text:story.history + '\n\n' + story.detail, facts:[],
      sources:[{title:'Туристический портал Шанхая', url:story.source}]};
  };
})();

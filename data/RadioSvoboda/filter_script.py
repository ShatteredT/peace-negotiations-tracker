from datasets import load_dataset
from filter import filter_articles, save_to_file, filter_by_date

# слова для заголовків
TITLE_WORDS = ["переговор", "перемов", "зустрі", "мир", "промов",
               "референдум", "прибув", "підпис", "угод", "тристорон",
               "кластер", "коаліц", "обговор", "договір", "договор",
               "домов", "дипломат", "обмін", "перемир", ]

# слова для тегів
TAG_WORDS   = ["мир", "угод", "дипломат", "переговор",
               "зустрі", "тристорон", "трибунал", "обмін", "полонен", "домов",
               "перемир", "припинення вогню"]

# Як поєднувати умови для заголовків і тегів
COMBINE = "or"        # "or"  = слова є в заголовку АБО в тегах
                      # "and" = слова є і в заголовку, І в тегах

WAR_START = '2022-02-24'

if __name__ == "__main__":
    ds = load_dataset("Yuliana-K/RadioSvoboda_Article_Corpus", split="train")
    filtered = filter_articles(ds, TITLE_WORDS, TAG_WORDS, COMBINE)
    filtered_by_date = filter_by_date(filtered, date=WAR_START)
    save_to_file(filtered_by_date)